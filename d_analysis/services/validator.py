# d_analysis/services/validator.py

import pandas as pd
from numbers import Number

from d_analysis.models import DuplicateValue, Upload, ProcessingLog

class DataValidator:
    """This validates and cleans data rows, handles missing optional fields, and logs audit warnings."""

    def __init__(self, upload_instance: Upload):
        self.upload = upload_instance
        self.file_path = upload_instance.file.path

    def validate_and_clean_data(self):
        valid_sheets = getattr(self.upload, "sheets", None)
        if valid_sheets is None:
            return {}

        valid_sheets = list(valid_sheets.filter(is_valid_data_sheet=True))
        if not valid_sheets:
            raise ValueError("No data worksheets with detectable headers were found.")

        cleaned_data_cache = {}
        mapping_by_sheet = {}
        for mapping in self.upload.mappings.all():
            mapping_by_sheet.setdefault(mapping.sheet_name, {})[
                mapping.source_header.strip()
            ] = mapping.standardized_field

        with pd.ExcelFile(self.file_path) as xls:
            for sheet in valid_sheets:
                if sheet.header_row_index is None:
                    raise ValueError(f"No header row was detected in worksheet '{sheet.sheet_name}'.")
                df = pd.read_excel(
                    xls,
                    sheet_name=sheet.sheet_name,
                    header=sheet.header_row_index,
                )
                df.columns = [str(column).strip() for column in df.columns]
                self._record_duplicate_values(df, sheet)

                cleaned_rows = []
                sheet_mappings = mapping_by_sheet.get(sheet.sheet_name, {})
                for row_offset, values in enumerate(df.to_dict(orient='records')):
                    row_index = sheet.header_row_index + row_offset + 2
                    if all(pd.isna(value) for value in values.values()):
                        ProcessingLog.objects.create(
                            upload=self.upload,
                            severity='INFO',
                            sheet_name=sheet.sheet_name,
                            row_index=row_index,
                            message="Blank row skipped.",
                        )
                        continue

                    row_dict = {}
                    row_warnings = []
                    for source_header, value in values.items():
                        if pd.isna(value):
                            cleaned_value = None
                        elif isinstance(value, str):
                            cleaned_value = value.strip() or None
                        else:
                            cleaned_value = value

                        row_dict[source_header] = cleaned_value
                        standardized_field = sheet_mappings.get(source_header)
                        if not standardized_field:
                            continue

                        mapped_value = cleaned_value
                        if standardized_field in {'farm_area', 'harvest_current', 'harvest_previous'} and cleaned_value is not None:
                            numeric_value = pd.to_numeric(cleaned_value, errors='coerce')
                            if pd.isna(numeric_value):
                                row_warnings.append(
                                    f"'{source_header}' is not a valid number for {standardized_field}."
                                )
                                mapped_value = None
                            else:
                                mapped_value = float(numeric_value)

                        if standardized_field in row_dict and standardized_field != source_header:
                            row_warnings.append(
                                f"Multiple source columns map to '{standardized_field}'; the additional value was retained under its source header."
                            )
                        else:
                            row_dict[standardized_field] = mapped_value

                    for warning in row_warnings:
                        ProcessingLog.objects.create(
                            upload=self.upload,
                            severity='WARNING',
                            sheet_name=sheet.sheet_name,
                            row_index=row_index,
                            message=warning,
                        )

                    cleaned_rows.append(row_dict)

                cleaned_data_cache[sheet.sheet_name] = cleaned_rows

        return cleaned_data_cache

    def _record_duplicate_values(self, dataframe, sheet):
        for column in dataframe.columns:
            groups = {}
            for row_offset, value in enumerate(dataframe[column].tolist()):
                if pd.isna(value):
                    continue
                if isinstance(value, str):
                    value = value.strip()
                    if not value:
                        continue

                if isinstance(value, bool):
                    key = ('boolean', value)
                elif isinstance(value, Number):
                    key = ('number', value)
                else:
                    key = (type(value).__name__, value)

                group = groups.setdefault(key, {'value': value, 'row_indices': []})
                group['row_indices'].append(sheet.header_row_index + row_offset + 2)

            for group in groups.values():
                if len(group['row_indices']) < 2:
                    continue
                DuplicateValue.objects.create(
                    upload=self.upload,
                    sheet_name=sheet.sheet_name,
                    column_name=str(column),
                    value=str(group['value']),
                    occurrence_count=len(group['row_indices']),
                    row_indices=group['row_indices'],
                )