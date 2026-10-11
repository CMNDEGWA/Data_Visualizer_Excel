from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.views.decorators.http import require_POST
from d_analysis.models import Upload, ProcessingJob, ProcessingLog
from d_analysis.forms import ExcelUploadForm
from d_analysis.services.workbook_inspector import WorkbookInspector
from d_analysis.services.column_mapper import ColumnMapper
from d_analysis.services.validator import DataValidator
from d_analysis.services.metrics import MetricsCalculator

# Create your views here.
def upload_view(request):
    """Handles workbook upload and triggers the ingestion service pipeline."""
    if request.method == 'POST':
        form = ExcelUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = request.FILES['file']
            
            # Create Upload instance
            upload_instance = Upload.objects.create(
                original_filename=uploaded_file.name,
                file=uploaded_file,
                file_size=uploaded_file.size,
                status='PROCESSING'
            )
            
            # Create Processing Job tracking record
            job = ProcessingJob.objects.create(
                upload=upload_instance,
                status='RUNNING',
                started_at=timezone.now()
            )

            try:
                with transaction.atomic():
                    inspector = WorkbookInspector(upload_instance)
                    inspector.inspect_workbook()

                    mapper = ColumnMapper(upload_instance)
                    mapper.auto_map_columns()

                    validator = DataValidator(upload_instance)
                    cleaned_data = validator.validate_and_clean_data()

                    calculator = MetricsCalculator(upload_instance, cleaned_data)
                    calculator.compute_and_save_metrics()

                    upload_instance.status = 'COMPLETED'
                    upload_instance.save(update_fields=['status'])
                    job.status = 'SUCCESS'
                    job.completed_at = timezone.now()
                    job.save(update_fields=['status', 'completed_at'])

                return redirect('dashboard_view', upload_id=upload_instance.id)

            except Exception as e:
                error_message = str(e)
                job.status = 'ERROR'
                job.error_message = error_message
                job.completed_at = timezone.now()
                job.save()
                upload_instance.status = 'FAILED'
                upload_instance.save()
                ProcessingLog.objects.create(
                    upload=upload_instance,
                    severity='ERROR',
                    message=f"Workbook processing failed: {error_message}",
                )
                error = error_message
        else:
            error = None
    else:
        form = ExcelUploadForm()
        error = None

    recent_uploads = Upload.objects.all().order_by('-uploaded_at')[:5]
    return render(request, 'd_analysis/upload.html', {
        'form': form,
        'error': error,
        'recent_uploads': recent_uploads,
    })


def dashboard_view(request, upload_id):
    """Renders the executive summary dashboard with KPIs and logs."""
    upload = get_object_or_404(Upload, id=upload_id)
    sheets = upload.sheets.all().order_by('sheet_name')
    metrics = upload.metrics.all().order_by('category', 'metric_name')
    logs = upload.logs.all().order_by('-created_at')[:20]
    mappings = upload.mappings.all().order_by('sheet_name', 'source_header')
    overview_metrics = metrics.filter(category='Overview')
    detail_metrics = metrics.exclude(category='Overview')
    duplicate_findings = [
        {
            'sheet_name': finding.sheet_name,
            'column_name': finding.column_name,
            'value': finding.value,
            'occurrence_count': finding.occurrence_count,
            'row_indices': finding.row_indices[:20],
            'additional_locations': max(0, len(finding.row_indices) - 20),
        }
        for finding in upload.duplicate_values.all()
    ]
    sheet_chart_data = [
        {
            'sheet_name': sheet.sheet_name,
            'row_count': sheet.row_count,
            'is_valid_data_sheet': sheet.is_valid_data_sheet,
        }
        for sheet in sheets
    ]

    context = {
        'upload': upload,
        'job': getattr(upload, 'job', None),
        'sheets': sheets,
        'overview_metrics': overview_metrics,
        'detail_metrics': detail_metrics,
        'duplicate_findings': duplicate_findings,
        'sheet_chart_data': sheet_chart_data,
        'logs': logs,
        'mappings': mappings,
    }
    return render(request, 'd_analysis/dashboard.html', context)


@require_POST
def delete_upload_view(request, upload_id):
    upload = get_object_or_404(Upload, id=upload_id)
    if upload.status not in {'COMPLETED', 'FAILED'}:
        messages.error(request, 'Only completed or failed workbooks can be deleted.')
        return redirect('upload_view')

    stored_file = upload.file
    with transaction.atomic():
        upload.delete()
        transaction.on_commit(lambda: stored_file.delete(save=False))

    messages.success(request, 'Workbook and its processing history were deleted.')
    return redirect('upload_view')