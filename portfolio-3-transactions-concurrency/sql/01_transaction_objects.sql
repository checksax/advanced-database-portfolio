-- =====================================================================
-- St. Grace Specialist Hospital - Portfolio 3 transactional objects
--
--   1. audit_log + generic audit trigger (who changed what, in which transaction)
--   2. Business transactions as PL/pgSQL functions. Each one is a single
--      atomic unit: if any step fails, every step is undone.
--        dispense_prescription  - stock decrement, ordered row locks (no deadlocks)
--        admit_patient          - bed allocation with FOR UPDATE SKIP LOCKED
--        record_payment         - pessimistic lock on the invoice, no overpayment
--        book_appointment       - relies on the EXCLUDE constraint, friendly error
--
-- Run after Portfolio 1 (re-run it if the Portfolio 1 generator is re-run,
-- because that drops and rebuilds the hospital schema):
--   psql -U hospital_admin -d stgrace_hospital -f 01_transaction_objects.sql
-- =====================================================================
SET search_path TO hospital, public;

-- ---------------------------------------------------------------------
-- 1. Audit trail
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_name  TEXT        NOT NULL,
    operation   TEXT        NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
    row_key     TEXT        NOT NULL,
    old_data    JSONB,
    new_data    JSONB,
    changed_by  TEXT        NOT NULL DEFAULT current_user,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    txid        BIGINT      NOT NULL DEFAULT txid_current()   -- groups rows written by one transaction
);

-- Generic row-level trigger. TG_ARGV[0] names the key column of the audited table.
-- For UPDATE only the columns that actually changed are stored.
CREATE OR REPLACE FUNCTION audit_row_change() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    old_j JSONB := CASE WHEN TG_OP IN ('UPDATE', 'DELETE') THEN to_jsonb(OLD) END;
    new_j JSONB := CASE WHEN TG_OP IN ('UPDATE', 'INSERT') THEN to_jsonb(NEW) END;
    k     TEXT;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        FOR k IN SELECT key FROM jsonb_each(new_j) LOOP
            IF old_j -> k = new_j -> k THEN
                old_j := old_j - k;
                new_j := new_j - k;
            END IF;
        END LOOP;
        IF new_j = '{}'::jsonb THEN
            RETURN NEW;                     -- nothing really changed
        END IF;
    END IF;
    INSERT INTO audit_log (table_name, operation, row_key, old_data, new_data)
    VALUES (TG_TABLE_NAME, TG_OP,
            COALESCE(to_jsonb(NEW), to_jsonb(OLD)) ->> TG_ARGV[0],
            old_j, new_j);
    RETURN COALESCE(NEW, OLD);
END $$;

DROP TRIGGER IF EXISTS trg_audit_drugs ON drugs;
CREATE TRIGGER trg_audit_drugs AFTER UPDATE ON drugs
    FOR EACH ROW EXECUTE FUNCTION audit_row_change('drug_id');

DROP TRIGGER IF EXISTS trg_audit_payments ON payments;
CREATE TRIGGER trg_audit_payments AFTER INSERT OR DELETE ON payments
    FOR EACH ROW EXECUTE FUNCTION audit_row_change('payment_id');

DROP TRIGGER IF EXISTS trg_audit_invoices ON invoices;
CREATE TRIGGER trg_audit_invoices AFTER UPDATE ON invoices
    FOR EACH ROW EXECUTE FUNCTION audit_row_change('invoice_id');

DROP TRIGGER IF EXISTS trg_audit_admissions ON admissions;
CREATE TRIGGER trg_audit_admissions AFTER INSERT OR UPDATE ON admissions
    FOR EACH ROW EXECUTE FUNCTION audit_row_change('admission_id');

-- ---------------------------------------------------------------------
-- 2a. Dispense a prescription
-- Locks drug rows in ascending drug_id order. Every caller takes locks in
-- the same order, so two pharmacists can never deadlock each other.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dispense_prescription(p_prescription_id INT, p_pharmacist_id INT)
RETURNS INT
LANGUAGE plpgsql AS $$
DECLARE
    v_status  TEXT;
    v_item    RECORD;
    v_stock   INT;
    v_lines   INT := 0;
BEGIN
    SELECT status INTO v_status
    FROM prescriptions WHERE prescription_id = p_prescription_id
    FOR UPDATE;                                         -- one pharmacist per prescription
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Prescription % does not exist', p_prescription_id;
    END IF;
    IF v_status <> 'Pending' THEN
        RAISE EXCEPTION 'Prescription % is already %', p_prescription_id, v_status;
    END IF;

    FOR v_item IN
        SELECT pi.drug_id, d.generic_name, pi.quantity - pi.dispensed_quantity AS needed
        FROM prescription_items pi JOIN drugs d USING (drug_id)
        WHERE pi.prescription_id = p_prescription_id
        ORDER BY pi.drug_id                              -- consistent lock order
    LOOP
        SELECT stock_quantity INTO v_stock
        FROM drugs WHERE drug_id = v_item.drug_id
        FOR UPDATE;
        IF v_stock < v_item.needed THEN
            RAISE EXCEPTION 'Insufficient stock of %: need %, have %',
                            v_item.generic_name, v_item.needed, v_stock
                  USING ERRCODE = 'check_violation';
        END IF;
        UPDATE drugs SET stock_quantity = stock_quantity - v_item.needed
        WHERE drug_id = v_item.drug_id;
        UPDATE prescription_items SET dispensed_quantity = quantity
        WHERE prescription_id = p_prescription_id AND drug_id = v_item.drug_id;
        v_lines := v_lines + 1;
    END LOOP;

    UPDATE prescriptions
    SET status = 'Dispensed', dispensed_by = p_pharmacist_id, dispensed_at = now()
    WHERE prescription_id = p_prescription_id;
    RETURN v_lines;
END $$;

-- ---------------------------------------------------------------------
-- 2b. Admit a patient to the first free bed of a ward type
-- SKIP LOCKED: a bed another nurse is assigning right now is skipped
-- instead of waiting for it, so concurrent admissions never queue or collide.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION admit_patient(p_patient_id INT, p_ward_type TEXT,
                                         p_doctor_id INT, p_reason TEXT)
RETURNS INT
LANGUAGE plpgsql AS $$
DECLARE
    v_bed_id       INT;
    v_admission_id INT;
BEGIN
    SELECT b.bed_id INTO v_bed_id
    FROM beds b JOIN wards w USING (ward_id)
    WHERE w.ward_type = p_ward_type AND b.status = 'Available'
    ORDER BY b.bed_id
    LIMIT 1
    FOR UPDATE OF b SKIP LOCKED;
    IF v_bed_id IS NULL THEN
        RAISE EXCEPTION 'No available bed in % ward', p_ward_type;
    END IF;

    INSERT INTO admissions (patient_id, bed_id, admitting_doctor_id, admission_reason)
    VALUES (p_patient_id, v_bed_id, p_doctor_id, p_reason)
    RETURNING admission_id INTO v_admission_id;

    UPDATE beds SET status = 'Occupied' WHERE bed_id = v_bed_id;

    INSERT INTO encounters (patient_id, doctor_id, admission_id, encounter_type, chief_complaint)
    VALUES (p_patient_id, p_doctor_id, v_admission_id, 'Inpatient', p_reason);

    RETURN v_admission_id;
END $$;

-- ---------------------------------------------------------------------
-- 2c. Record a payment
-- FOR UPDATE on the invoice serialises cashiers working on the same bill,
-- so the balance check and the insert cannot be interleaved (no overpayment).
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION record_payment(p_invoice_id INT, p_amount NUMERIC,
                                          p_method TEXT, p_staff_id INT)
RETURNS TEXT
LANGUAGE plpgsql AS $$
DECLARE
    v_total   NUMERIC;
    v_paid    NUMERIC;
    v_balance NUMERIC;
    v_status  TEXT;
BEGIN
    PERFORM 1 FROM invoices WHERE invoice_id = p_invoice_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Invoice % does not exist', p_invoice_id;
    END IF;

    SELECT COALESCE(SUM(line_total), 0) INTO v_total FROM invoice_items WHERE invoice_id = p_invoice_id;
    SELECT COALESCE(SUM(amount), 0)     INTO v_paid  FROM payments      WHERE invoice_id = p_invoice_id;
    v_balance := v_total - v_paid;

    IF p_amount > v_balance THEN
        RAISE EXCEPTION 'Payment of % exceeds outstanding balance of % on invoice %',
                        p_amount, v_balance, p_invoice_id
              USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO payments (invoice_id, amount, method, received_by)
    VALUES (p_invoice_id, p_amount, p_method, p_staff_id);

    v_status := CASE WHEN v_balance - p_amount = 0 THEN 'Paid' ELSE 'Partial' END;
    UPDATE invoices SET status = v_status WHERE invoice_id = p_invoice_id;
    RETURN v_status;
END $$;

-- ---------------------------------------------------------------------
-- 2d. Book an appointment
-- The EXCLUDE constraint does the concurrency control; this wrapper turns
-- the low-level error into a message a receptionist understands.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION book_appointment(p_patient_id INT, p_doctor_id INT,
                                            p_start TIMESTAMP, p_minutes INT DEFAULT 30)
RETURNS INT
LANGUAGE plpgsql AS $$
DECLARE
    v_id INT;
BEGIN
    INSERT INTO appointments (patient_id, doctor_id, scheduled_start, scheduled_end)
    VALUES (p_patient_id, p_doctor_id, p_start, p_start + make_interval(mins => p_minutes))
    RETURNING appointment_id INTO v_id;
    RETURN v_id;
EXCEPTION
    WHEN exclusion_violation THEN
        RAISE EXCEPTION 'Doctor % is already booked at %', p_doctor_id, p_start
              USING ERRCODE = 'exclusion_violation';
END $$;
