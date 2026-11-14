# Rebuilds the hospital database, loads data, and captures evidence output.
# Prerequisite (once): psql -U postgres -f sql\00_create_database.sql
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$psql = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
$env:PGPASSWORD = "hospital_pass"    # local development account only
$conn = @("-h", "localhost", "-U", "hospital_admin", "-d", "stgrace_hospital")

New-Item -ItemType Directory -Force "$here\evidence" | Out-Null

python "$here\..\data-generator\generate_hospital_data.py" | Tee-Object "$here\evidence\data_load_output.txt"
& $psql @conn -f "$here\sql\03_constraint_tests.sql" *> "$here\evidence\constraint_tests_output.txt"
& $psql @conn -f "$here\sql\04_data_profile.sql"     *> "$here\evidence\data_profile_output.txt"
& $psql @conn -c "\d+ hospital.appointments"          *> "$here\evidence\appointments_table_definition.txt"

Write-Host "Evidence written to $here\evidence"
