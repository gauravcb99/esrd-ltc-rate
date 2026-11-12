#!/usr/bin/env python3
"""
Hemodialysis Vascular Access: Long-term Catheter Rate (CBE ID 2978)
The published CMS specification, implemented in SQL and run on SYNTHETIC patients.

Every patient, facility, date and claim in this file is made up for testing.
Nothing here comes from EQRS, Medicare claims or any real facility.

Specification followed
  [TS] CMS ESRD QIP CY 2026 Measure Technical Specifications (last revised
       April 29, 2026), "Hemodialysis Vascular Access: Long-term Catheter Rate
       (Clinical Measure)", pages 27-28.
  [MM] CMS ESRD Measures Manual for the 2026 Performance Period, Version 11.2,
       section 2.1 (2.1.6 to 2.1.12).

Scope
  Implemented: the patient-month denominator, patient exclusions 1 to 5, the
      numerator, and the 11-eligible-patient facility minimum.
  Taken as inputs (derived upstream in real processing, MM sections 3.1-3.2):
      which facility treated the patient for the complete month, whether the
      patient was on HD for the complete month, whether the patient is on ESRD
      treatment, and which claims fall in each exclusion category.
  Not implemented: ECE months, the new-facility CCN rule, QIP scoring.

Run:  python3 ltc_rate_spec_test.py      (Python 3.8+, standard library only)
"""
import sqlite3

REPORTING_YEAR = 2026          # performance period CY 2026 (payment year 2028)
MIN_ELIGIBLE_PATIENTS = 11     # TS facility exclusion 1

# ---------------------------------------------------------------------------
# 1. The measure logic. One CTE per step of the specification.
# ---------------------------------------------------------------------------
MEASURE_SQL = """
WITH
pm AS (
    -- One row per patient-month, with a running month number so that
    -- "previous month" still works across the December/January boundary.
    SELECT m.patient_id, m.month, m.facility_id, m.modality,
           CAST(substr(m.month, 1, 4) AS INTEGER) * 12
             + CAST(substr(m.month, 6, 2) AS INTEGER)            AS month_no,
           p.esrd_treatment,
           -- MM 2.1.10: age = first day of the reporting month minus date of birth
           (CAST(substr(m.month, 1, 4) AS INTEGER)
              - CAST(substr(p.birth_date, 1, 4) AS INTEGER))
             - (substr(m.month, 6, 2) || '-01' < substr(p.birth_date, 6, 5))
                                                                 AS age_first_day
    FROM patient_month m
    JOIN patient p ON p.patient_id = m.patient_id
),
ranked_report AS (
    -- TS additional information 1-2, MM 2.1.12: access type comes from EQRS
    -- only. Use the last type the assigned facility reported in the month.
    -- If the assigned facility reported none, use the last type reported by
    -- any other facility.
    SELECT pm.patient_id, pm.month, a.access_type,
           ROW_NUMBER() OVER (
               PARTITION BY pm.patient_id, pm.month
               ORDER BY COALESCE(a.facility_id = pm.facility_id, 0) DESC,
                        a.report_date DESC)                      AS rn
    FROM pm
    JOIN access_report a
      ON a.patient_id = pm.patient_id
     AND substr(a.report_date, 1, 7) = pm.month
),
access AS (
    -- MM 2.1.12: these five values, or a missing value, mean a catheter is
    -- considered in use for the month.
    SELECT pm.*, r.access_type,
           CASE WHEN r.access_type IS NULL
                  OR trim(r.access_type) = ''
                  OR r.access_type IN ('AVFWCATHETER', 'AVGWCATHETER',
                                       'CATHETERONLY', 'PORTONLY',
                                       'OTHERUNKNOWN')
                THEN 1 ELSE 0 END                                AS catheter_in_use,
           -- TS denominator, MM 2.1.11: HD for the complete month at one facility
           CASE WHEN pm.facility_id IS NOT NULL AND pm.modality = 'HD'
                THEN 1 ELSE 0 END                                AS hd_month_at_facility
    FROM pm
    LEFT JOIN ranked_report r
      ON r.patient_id = pm.patient_id AND r.month = pm.month AND r.rn = 1
),
flags AS (
    SELECT c.*,
           -- TS numerator, TS additional information 3, MM 2.1.12: catheter in
           -- the reporting month and the two months before it, each a complete
           -- HD month at the same facility. Looking back by month number lets
           -- November and December of the prior year count for January and
           -- February (TS additional information 6).
           CASE WHEN c.catheter_in_use = 1 AND c.hd_month_at_facility = 1
                 AND EXISTS (SELECT 1 FROM access p1
                             WHERE p1.patient_id = c.patient_id
                               AND p1.month_no = c.month_no - 1
                               AND p1.facility_id = c.facility_id
                               AND p1.hd_month_at_facility = 1
                               AND p1.catheter_in_use = 1)
                 AND EXISTS (SELECT 1 FROM access p2
                             WHERE p2.patient_id = c.patient_id
                               AND p2.month_no = c.month_no - 2
                               AND p2.facility_id = c.facility_id
                               AND p2.hd_month_at_facility = 1
                               AND p2.catheter_in_use = 1)
                THEN 1 ELSE 0 END                                AS long_term_catheter,
           -- TS patient exclusion 4a-4d, MM 2.1.9-2.1.10: hospice claim in the
           -- reporting month, or one of three conditions on a claim in the past
           -- 12 months (here: the reporting month or the 11 months before it).
           CASE WHEN EXISTS (SELECT 1 FROM claim k
                             WHERE k.patient_id = c.patient_id
                               AND k.category = 'HOSPICE'
                               AND substr(k.claim_date, 1, 7) = c.month)
                  OR EXISTS (SELECT 1 FROM claim k
                             WHERE k.patient_id = c.patient_id
                               AND k.category IN ('METASTATIC_CANCER', 'ESLD',
                                                  'COMA_ANOXIC')
                               AND CAST(substr(k.claim_date, 1, 4) AS INTEGER) * 12
                                     + CAST(substr(k.claim_date, 6, 2) AS INTEGER)
                                   BETWEEN c.month_no - 11 AND c.month_no)
                THEN 1 ELSE 0 END                                AS limited_life_expectancy
    FROM access c
)
SELECT patient_id, month, facility_id, access_type,
       CASE
         WHEN esrd_treatment = 0              THEN '5'  -- TS excl. 5: not on ESRD treatment
         WHEN age_first_day < 18              THEN '1'  -- TS excl. 1: under 18
         WHEN modality = 'NOT_HD'             THEN '2'  -- TS excl. 2: not on HD
         WHEN hd_month_at_facility = 0        THEN '3'  -- TS excl. 3: not a complete HD month at one facility
         WHEN catheter_in_use = 1
          AND limited_life_expectancy = 1     THEN '4'  -- TS excl. 4: catheter and limited life expectancy
         WHEN long_term_catheter = 1          THEN 'N'  -- denominator and numerator
         ELSE                                      'D'  -- denominator only
       END AS status
FROM flags
WHERE substr(month, 1, 4) = :year
ORDER BY patient_id, month
"""

# ---------------------------------------------------------------------------
# 2. Synthetic test patients. All invented.
#    stay:    (first month, last month, facility that treated the patient for
#              the complete month or None, modality for the month)
#             modality is HD (complete month), HD_PARTIAL or NOT_HD
#    access:  (first month, last month, EQRS access type) -> one report on the
#             15th of each month from the assigned facility
#    reports: extra dated reports (date, reporting facility, access type)
#    claims:  (claim date, category)
#    expect:  expected status for Jan..Dec of the reporting year
#             N numerator, D denominator only, 1-5 exclusion number,
#             . no patient-month (not yet a patient)
# ---------------------------------------------------------------------------
A, B = "SYN-FAC-A", "SYN-FAC-B"
PATIENTS = [
    dict(id="SYN-P01", dob="1958-03-14", esrd=1,
         tests="Fistula all year (baseline)",
         stay=[("2025-11", "2026-12", A, "HD")],
         access=[("2025-11", "2026-12", "AVFONLY")],
         expect="DDDDDDDDDDDD"),
    dict(id="SYN-P02", dob="1949-09-02", esrd=1,
         tests="Catheter since Nov 2025; prior Nov-Dec count toward Jan-Feb",
         stay=[("2025-11", "2026-12", A, "HD")],
         access=[("2025-11", "2026-12", "CATHETERONLY")],
         expect="NNNNNNNNNNNN"),
    dict(id="SYN-P03", dob="1966-01-25", esrd=1,
         tests="Starts HD Feb 10 with a catheter; fistula with catheter "
               "May-Jun; two reports in Jul, the last is fistula only",
         stay=[("2026-02", "2026-02", None, "HD_PARTIAL"),
               ("2026-03", "2026-12", A, "HD")],
         access=[("2026-03", "2026-04", "CATHETERONLY"),
                 ("2026-05", "2026-06", "AVFWCATHETER"),
                 ("2026-08", "2026-12", "AVFONLY")],
         reports=[("2026-02-12", A, "CATHETERONLY"),
                  ("2026-07-07", A, "AVFWCATHETER"),
                  ("2026-07-28", A, "AVFONLY")],
         expect=".3DDNNDDDDDD"),
    dict(id="SYN-P04", dob="1971-07-08", esrd=0,
         tests="HD with a catheter but no ESRD treatment record",
         stay=[("2025-11", "2026-12", A, "HD")],
         access=[("2025-11", "2026-12", "CATHETERONLY")],
         expect="555555555555"),
    dict(id="SYN-P05", dob="1955-11-30", esrd=1,
         tests="Fistula, but no access type reported Jul-Sep",
         stay=[("2025-11", "2026-12", A, "HD")],
         access=[("2025-11", "2026-06", "AVFONLY"),
                 ("2026-10", "2026-12", "AVFONLY")],
         expect="DDDDDDDDNDDD"),
    dict(id="SYN-P06", dob="1962-04-19", esrd=1,
         tests="Catheter through Mar; no Apr report from assigned facility, "
               "another facility reports fistula only",
         stay=[("2025-11", "2026-12", A, "HD")],
         access=[("2025-11", "2026-03", "CATHETERONLY"),
                 ("2026-05", "2026-12", "AVFONLY")],
         reports=[("2026-04-16", B, "AVFONLY")],
         expect="NNNDDDDDDDDD"),
    dict(id="SYN-P07", dob="1953-08-11", esrd=1,
         tests="Catheter all year; last day at facility A is May 31, first "
               "day at facility B is Jun 1",
         stay=[("2025-11", "2026-05", A, "HD"),
               ("2026-06", "2026-12", B, "HD")],
         access=[("2025-11", "2026-12", "CATHETERONLY")],
         expect="NNNNNDDNNNNN"),
    dict(id="SYN-P08", dob="1960-12-05", esrd=1,
         tests="On PD through Mar; switches to HD with a catheter on Apr 20",
         stay=[("2025-11", "2026-03", A, "NOT_HD"),
               ("2026-04", "2026-04", A, "HD_PARTIAL"),
               ("2026-05", "2026-12", A, "HD")],
         access=[("2026-05", "2026-12", "CATHETERONLY")],
         reports=[("2026-04-22", A, "CATHETERONLY")],
         expect="2223DDNNNNNN"),
    dict(id="SYN-P09", dob="2008-06-15", esrd=1,
         tests="Fistula; turns 18 on Jun 15, so first adult month is Jul",
         stay=[("2025-11", "2026-12", A, "HD")],
         access=[("2025-11", "2026-12", "AVFONLY")],
         expect="111111DDDDDD"),
    dict(id="SYN-P10", dob="1947-02-21", esrd=1,
         tests="Catheter; metastatic cancer claims Dec 2025 and Jul 2026",
         stay=[("2025-11", "2026-12", A, "HD")],
         access=[("2025-11", "2026-12", "CATHETERONLY")],
         claims=[("2025-12-09", "METASTATIC_CANCER"),
                 ("2026-07-14", "METASTATIC_CANCER")],
         expect="444444444444"),
    dict(id="SYN-P11", dob="1959-10-17", esrd=1,
         tests="Fistula; end stage liver disease claims Dec 2025 and Jun 2026",
         stay=[("2025-11", "2026-12", A, "HD")],
         access=[("2025-11", "2026-12", "AVFONLY")],
         claims=[("2025-12-02", "ESLD"), ("2026-06-18", "ESLD")],
         expect="DDDDDDDDDDDD"),
    dict(id="SYN-P12", dob="1944-05-03", esrd=1,
         tests="Catheter; hospice claims in Nov and Dec",
         stay=[("2025-11", "2026-12", A, "HD")],
         access=[("2025-11", "2026-12", "CATHETERONLY")],
         claims=[("2026-11-04", "HOSPICE"), ("2026-12-02", "HOSPICE")],
         expect="NNNNNNNNNN44"),
]

# Three more checks that are run but not shown in the main table.
EXTRA_CHECKS = [
    dict(id="SYN-X03", dob="1950-01-01", esrd=1,
         tests="Catheter; transfers from facility A to B on Jun 15, so Jun is "
               "not a complete month at either facility",
         stay=[("2025-11", "2026-05", A, "HD"),
               ("2026-06", "2026-06", None, "HD"),
               ("2026-07", "2026-12", B, "HD")],
         access=[("2025-11", "2026-05", "CATHETERONLY"),
                 ("2026-07", "2026-12", "CATHETERONLY")],
         reports=[("2026-06-05", A, "CATHETERONLY"),
                  ("2026-06-25", B, "CATHETERONLY")],
         expect="NNNNN3DDNNNN"),
    dict(id="SYN-X01", dob="1950-01-01", esrd=1,
         tests="Catheter; coma or anoxic brain injury claims (same code path "
               "as the other two claim-based conditions)",
         stay=[("2025-11", "2026-12", A, "HD")],
         access=[("2025-11", "2026-12", "CATHETERONLY")],
         claims=[("2025-12-20", "COMA_ANOXIC"), ("2026-07-01", "COMA_ANOXIC")],
         expect="444444444444"),
    dict(id="SYN-X02", dob="1950-01-01", esrd=1,
         tests="Port access only and other/unknown also count as catheter",
         stay=[("2025-11", "2026-12", A, "HD")],
         access=[("2025-11", "2026-04", "PORTONLY"),
                 ("2026-05", "2026-12", "OTHERUNKNOWN")],
         expect="NNNNNNNNNNNN"),
]

# ---------------------------------------------------------------------------
# 3. Load the synthetic data, run the measure, compare with expected results.
# ---------------------------------------------------------------------------
def month_range(first, last):
    y, m = (int(x) for x in first.split("-"))
    y2, m2 = (int(x) for x in last.split("-"))
    out = []
    while (y, m) <= (y2, m2):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def load(patients):
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE patient       (patient_id TEXT PRIMARY KEY, birth_date TEXT,
                                    esrd_treatment INTEGER);
        CREATE TABLE patient_month (patient_id TEXT, month TEXT, facility_id TEXT,
                                    modality TEXT);
        CREATE TABLE access_report (patient_id TEXT, facility_id TEXT,
                                    report_date TEXT, access_type TEXT);
        CREATE TABLE claim         (patient_id TEXT, claim_date TEXT, category TEXT);
    """)
    for p in patients:
        db.execute("INSERT INTO patient VALUES (?,?,?)", (p["id"], p["dob"], p["esrd"]))
        facility_in = {}
        for first, last, facility, modality in p["stay"]:
            for month in month_range(first, last):
                facility_in[month] = facility
                db.execute("INSERT INTO patient_month VALUES (?,?,?,?)",
                           (p["id"], month, facility, modality))
        for first, last, access_type in p.get("access", []):
            for month in month_range(first, last):
                db.execute("INSERT INTO access_report VALUES (?,?,?,?)",
                           (p["id"], facility_in[month], month + "-15", access_type))
        for report_date, facility, access_type in p.get("reports", []):
            db.execute("INSERT INTO access_report VALUES (?,?,?,?)",
                       (p["id"], facility, report_date, access_type))
        for claim_date, category in p.get("claims", []):
            db.execute("INSERT INTO claim VALUES (?,?,?)", (p["id"], claim_date, category))
    return db


def run(patients):
    """Return {patient_id: {month: (status, facility_id)}} for the reporting year."""
    db = load(patients)
    out = {p["id"]: {} for p in patients}
    for pid, month, facility, _access, status in db.execute(
            MEASURE_SQL, {"year": str(REPORTING_YEAR)}):
        out[pid][month] = (status, facility)
    return out


def strip(months_for_patient):
    year_months = month_range(f"{REPORTING_YEAR}-01", f"{REPORTING_YEAR}-12")
    return "".join(months_for_patient.get(m, (".", None))[0] for m in year_months)


def main():
    results = run(PATIENTS)
    failures = 0

    print("Long-term Catheter Rate (CBE 2978): test cases on SYNTHETIC patients")
    print(f"Reporting year {REPORTING_YEAR}; lookback uses Nov-Dec {REPORTING_YEAR - 1}")
    print("N = denominator and numerator   D = denominator only")
    print("1-5 = excluded, by patient exclusion number in the CY 2026 technical "
          "specification   . = no patient-month")
    print()
    print(f"{'Patient':8} {'JFMAMJJASOND':12} {'Den':>3} {'Num':>3} {'Excl':>4}  "
          f"{'Check':5}  What the case tests")
    for p in PATIENTS:
        got = strip(results[p["id"]])
        ok = got == p["expect"]
        failures += not ok
        den = sum(ch in "ND" for ch in got)
        num = got.count("N")
        excl = sum(ch in "12345" for ch in got)
        print(f"{p['id']:8} {got:12} {den:>3} {num:>3} {excl:>4}  "
              f"{'pass' if ok else 'FAIL':5}  {p['tests']}")
        if not ok:
            print(f"{'':8} {p['expect']:12} <- expected")

    # Facility roll-up (TS minimum data requirement; no rate is printed for a
    # facility below the minimum).
    print()
    print("Facility roll-up (synthetic facilities)")
    facilities = {}
    for pid, by_month in results.items():
        for status, facility in by_month.values():
            if status in "ND":
                f = facilities.setdefault(facility, dict(den=0, num=0, pts=set()))
                f["den"] += 1
                f["num"] += status == "N"
                f["pts"].add(pid)
    for facility in sorted(facilities):
        f = facilities[facility]
        n = len(f["pts"])
        if n >= MIN_ELIGIBLE_PATIENTS:
            outcome = f"rate = {100 * f['num'] / f['den']:.1f}%"
        else:
            plural = "s" if n != 1 else ""
            outcome = (f"not scored: {n} eligible patient{plural}, below the minimum "
                       f"of {MIN_ELIGIBLE_PATIENTS}")
        print(f"  {facility}: denominator {f['den']} patient-months, "
              f"numerator {f['num']}, {outcome}")

    # Checks on the roll-up, worked out by hand from the expected strips.
    expected_rollup = {A: (99, 39, 10), B: (7, 5, 1)}
    for facility, (den, num, pts) in expected_rollup.items():
        f = facilities[facility]
        if (f["den"], f["num"], len(f["pts"])) != (den, num, pts):
            failures += 1
            print(f"  FAIL roll-up for {facility}")

    print()
    print("Additional checks (not in the table)")
    extra = run(EXTRA_CHECKS)
    for p in EXTRA_CHECKS:
        got = strip(extra[p["id"]])
        ok = got == p["expect"]
        failures += not ok
        print(f"  {p['id']} {got}  {'pass' if ok else 'FAIL'}  {p['tests']}")

    print()
    print("ALL CHECKS PASS" if failures == 0 else f"{failures} CHECK(S) FAILED")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
