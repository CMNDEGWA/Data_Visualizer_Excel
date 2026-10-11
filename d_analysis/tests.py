from io import BytesIO
import os
from tempfile import TemporaryDirectory

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from openpyxl import Workbook

from d_analysis.forms import ExcelUploadForm
from d_analysis.models import MetricSnapshot, ProcessingJob, ProcessingLog, SheetInspection, Upload


def workbook_upload(name, sheets):
	workbook = Workbook()
	first_sheet = workbook.active
	workbook.remove(first_sheet)
	for sheet_name, rows in sheets:
		sheet = workbook.create_sheet(sheet_name)
		for row in rows:
			sheet.append(row)

	content = BytesIO()
	workbook.save(content)
	return SimpleUploadedFile(name, content.getvalue())


class UploadPipelineTests(TestCase):
	def setUp(self):
		self.media_directory = TemporaryDirectory()
		self.addCleanup(self.media_directory.cleanup)
		media_override = override_settings(MEDIA_ROOT=self.media_directory.name)
		media_override.enable()
		self.addCleanup(media_override.disable)

	def test_processes_whitespace_named_sheet_with_header_below_first_row(self):
		upload_file = workbook_upload(
			'field-data.xlsx',
			[
				(' Farm Data ', [
					['Farm register'],
					['Farm ID', 'Farm Area (ha)'],
					['F-001', 12.5],
					['F-002', 7.5],
				]),
				(' Dashboard', [
					['Summary', 'Records'],
					['Total farms', 2],
				]),
			],
		)

		response = self.client.post('/', {'file': upload_file})

		self.assertEqual(response.status_code, 302)
		upload = Upload.objects.get(original_filename='field-data.xlsx')
		self.assertEqual(upload.status, 'COMPLETED')
		self.assertEqual(upload.job.status, 'SUCCESS')

		data_sheet = upload.sheets.get(sheet_name=' Farm Data ')
		self.assertEqual(data_sheet.header_row_index, 1)
		self.assertTrue(data_sheet.is_valid_data_sheet)
		dashboard_sheet = upload.sheets.get(sheet_name=' Dashboard')
		self.assertFalse(dashboard_sheet.is_valid_data_sheet)

		self.assertTrue(
			upload.mappings.filter(
				sheet_name=' Farm Data ',
				source_header='Farm Area (ha)',
				standardized_field='farm_area',
			).exists()
		)
		area_metric = upload.metrics.get(metric_name='Total Area ( Farm Data )')
		self.assertEqual(area_metric.metric_value, 20.0)
		self.assertEqual(
			upload.metrics.get(metric_name='Total Records Analyzed').metric_value,
			2.0,
		)

	def test_dashboard_displays_persisted_workbook_and_processing_details(self):
		upload_file = workbook_upload(
			'regional-report.xlsx',
			[('Sales Data', [['Region', 'Amount'], ['North', 8], ['South', 6]])],
		)
		self.client.post('/', {'file': upload_file})
		upload = Upload.objects.get(original_filename='regional-report.xlsx')
		MetricSnapshot.objects.create(
			upload=upload,
			metric_name='Total Amount',
			metric_value=14,
			category='General',
			calculation_metadata={'field': 'amount', 'value_count': 2},
		)
		ProcessingLog.objects.create(
			upload=upload,
			severity='WARNING',
			sheet_name='Sales Data',
			row_index=3,
			column_name='Amount',
			message='Example validation warning.',
		)

		response = self.client.get(f'/dashboard/{upload.id}/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Upload status')
		self.assertContains(response, 'Processing job')
		self.assertContains(response, 'Worksheet Inspection')
		self.assertContains(response, 'Region, Amount')
		self.assertContains(response, 'Header row 1')
		self.assertContains(response, 'Column Mappings')
		self.assertContains(response, 'custom_amount')
		self.assertContains(response, 'Total Amount')
		self.assertContains(response, 'Field: amount')
		self.assertContains(response, 'Values used: 2')
		self.assertContains(response, 'Column Amount')
		self.assertContains(response, 'Example validation warning.')
		self.assertContains(response, 'id="sheet-data"')

	def test_rejects_workbook_without_any_data_sheets_and_keeps_failure_visible(self):
		upload_file = workbook_upload(
			'presentation-only.xlsx',
			[('Dashboard', [['Summary', 'Value'], ['Total', 1]])],
		)

		response = self.client.post('/', {'file': upload_file})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'No data worksheets with detectable headers were found.')
		upload = Upload.objects.get(original_filename='presentation-only.xlsx')
		self.assertEqual(upload.status, 'FAILED')
		self.assertEqual(upload.job.status, 'ERROR')
		self.assertEqual(upload.sheets.count(), 0)
		self.assertEqual(upload.metrics.count(), 0)
		self.assertTrue(
			ProcessingLog.objects.filter(upload=upload, severity='ERROR').exists()
		)
		self.assertIn(upload, response.context['recent_uploads'])

	def test_upload_form_accepts_xlsx_case_insensitively_and_rejects_xls(self):
		xlsx_form = ExcelUploadForm(files={
			'file': SimpleUploadedFile('workbook.XLSX', b'placeholder'),
		})
		xls_form = ExcelUploadForm(files={
			'file': SimpleUploadedFile('workbook.xls', b'placeholder'),
		})

		self.assertTrue(xlsx_form.is_valid())
		self.assertFalse(xls_form.is_valid())

	def test_delete_completed_upload_removes_file_and_related_records(self):
		upload = Upload.objects.create(
			original_filename='completed.xlsx',
			file=ContentFile(b'workbook', name='completed.xlsx'),
			file_size=8,
			status='COMPLETED',
		)
		ProcessingJob.objects.create(upload=upload, status='SUCCESS')
		SheetInspection.objects.create(upload=upload, sheet_name='Data')
		MetricSnapshot.objects.create(
			upload=upload,
			metric_name='Total Records Analyzed',
			metric_value=1,
		)
		file_path = upload.file.path

		with self.captureOnCommitCallbacks(execute=True):
			response = self.client.post(f'/uploads/{upload.id}/delete/', follow=True)

		self.assertRedirects(response, '/')
		self.assertFalse(Upload.objects.filter(id=upload.id).exists())
		self.assertFalse(ProcessingJob.objects.filter(upload_id=upload.id).exists())
		self.assertFalse(SheetInspection.objects.filter(upload_id=upload.id).exists())
		self.assertFalse(MetricSnapshot.objects.filter(upload_id=upload.id).exists())
		self.assertFalse(os.path.exists(file_path))
		self.assertContains(response, 'Workbook and its processing history were deleted.')

	def test_delete_rejects_processing_upload(self):
		upload = Upload.objects.create(
			original_filename='running.xlsx',
			file=ContentFile(b'workbook', name='running.xlsx'),
			file_size=8,
			status='PROCESSING',
		)

		response = self.client.post(f'/uploads/{upload.id}/delete/', follow=True)

		self.assertRedirects(response, '/')
		self.assertTrue(Upload.objects.filter(id=upload.id).exists())
		self.assertContains(response, 'Only completed or failed workbooks can be deleted.')
