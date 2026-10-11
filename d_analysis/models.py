import uuid
from django.db import models

# Create your models here.
class Upload(models.Model):
    """This tracks Raw Excel workbooks uploaded by users."""
    id = models.UUIDField(primary_key=True,
                          default=uuid.uuid4,
                          editable=False)
    original_filename = models.CharField(max_length=255)
    file = models.FileField(upload_to='uploads/')
    file_size = models.PositiveIntegerField(help_text='The File Size in Bytes.')
    checksum = models.CharField(max_length=64, blank=True, null=True)
    uploaded_at =models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=50,
        choices=[
            ('PENDING', 'Pending'),
            ('PROCESSING', 'Processing'),
            ('COMPLETED', 'Completed'),
            ('FAILED', 'Failed'),
        ],
        default='PENDING',
    )
    
    def __str__(self):
        return f"{self.original_filename} ({self.uploaded_at.strftime('%Y-%m-%d %H:%M:%S')})"

class ProcessingJob(models.Model):
    """This manages execution states of workbook parsing and analysis tasks."""
    upload = models.OneToOneField(Upload, on_delete=models.CASCADE, related_name='job')
    status = models.CharField(
        max_length=50,
        choices=[
            ('QUEUED', 'Queued'),
            ('RUNNING', 'Running'),
            ('SUCCESS', 'Success'),
            ('ERROR', 'Error'),
        ],
        default='QUEUED',
    )
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    processor_version = models.CharField(max_length=20, default='1.0.0')
    error_message = models.TextField(blank=True, null=True)
    
    def __str__(self):
        return f"Job {self.pk} - {self.status}"
    
class SheetInspection(models.Model):
    """This stores metadata discovered dynamically from individual workbook sheet."""
    upload = models.ForeignKey(Upload, on_delete=models.CASCADE, related_name='sheets')
    sheet_name = models.CharField(max_length=255)
    header_row_index = models.PositiveIntegerField(blank=True, null=True)
    row_count = models.PositiveIntegerField(default=0)
    detected_headers = models.JSONField(default=list, help_text="List of column headers found in the sheet.")
    is_valid_data_sheet = models.BooleanField(default=True)
    inspection_warnings = models.JSONField(default=list, blank=True)

    @property
    def display_name(self):
        return self.sheet_name.strip()
    
    def __str__(self):
        return f"{self.sheet_name} ({self.row_count} rows)"

class ColumnMapping(models.Model):
    """This maps incoming source headers to standardized core attributes"""
    upload = models.ForeignKey(Upload, on_delete=models.CASCADE, related_name='mappings')
    sheet_name = models.CharField(max_length=255)
    source_header = models.CharField(max_length=255)
    standardized_field = models.CharField(max_length=255)
    confidence_score = models.FloatField(default=1.0)
    mapping_version = models.CharField(max_length=20, default='1.0')

    @property
    def display_sheet_name(self):
        return self.sheet_name.strip()
    
    def __str__(self):
        return f"{self.source_header} -> {self.standardized_field}"
    
class MetricSnapshot(models.Model):
    """This persists computed KPIs and summary metrics for dashboards."""
    upload = models.ForeignKey(Upload, on_delete=models.CASCADE, related_name='metrics')
    metric_name = models.CharField(max_length=100)
    metric_value = models.FloatField()
    category = models.CharField(max_length=100, default='General')
    calculation_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.metric_name}: {self.metric_value}"

class DuplicateValue(models.Model):
    """This records repeated values found within a single worksheet column."""
    upload = models.ForeignKey(Upload, on_delete=models.CASCADE, related_name='duplicate_values')
    sheet_name = models.CharField(max_length=255)
    column_name = models.CharField(max_length=255)
    value = models.TextField()
    occurrence_count = models.PositiveIntegerField()
    row_indices = models.JSONField(default=list)

    class Meta:
        ordering = ['sheet_name', 'column_name', 'value']

    def __str__(self):
        return f"{self.sheet_name}.{self.column_name}: {self.value} ({self.occurrence_count})"
    
class ProcessingLog(models.Model):
    """This audits warnings, missing data flags and malformed rows during ingestion"""
    upload = models.ForeignKey(Upload, on_delete=models.CASCADE, related_name='logs')
    severity = models.CharField(
        max_length=20,
        choices=[
            ('INFO', 'Info'),
            ('WARNING', 'Warning'),
            ('ERROR', 'Error'),
        ],
        default='WARNING',
    )
    sheet_name = models.CharField(max_length=255, blank=True, null=True)
    row_index = models.PositiveIntegerField(blank=True, null=True)
    column_name = models.CharField(max_length=255, blank=True, null=True)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"[{self.severity}] Sheet: {self.sheet_name} - Row {self.row_index}"