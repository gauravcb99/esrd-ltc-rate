# Long-term Catheter Rate (CBE 2978): spec implementation and test cases

This folder implements one ESRD Quality Incentive Program clinical measure, the Hemodialysis Vascular Access: Long-term Catheter Rate, directly from CMS's published specification, and tests it on synthetic patients. The measure was developed by UM-KECC and is stewarded by CMS.

**Every patient, facility, date and claim here is synthetic.** No CMS, EQRS, Medicare or facility data is used.

## Sources followed
- CMS ESRD QIP CY 2026 Technical Measure Specifications (final, revised 04/29/2026), Long-term Catheter Rate, pages 27-28.
- CMS ESRD Measures Manual for the 2026 Performance Period, section 2.1.

## What the code does
- Builds patient-months and applies the denominator, the five patient exclusions, the numerator, and the 11-eligible-patient facility minimum, as one SQL query.
- Runs 12 synthetic patients (plus 3 extra checks) built so each rule is exercised at least once, and compares the output with expected results.
- Rolls results up to two synthetic facilities. Both fall under the 11-patient minimum, so both print "not scored" rather than a rate.

## Not covered
Extraordinary circumstances exception months, the new-facility rule, and QIP scoring. Complete-month facility assignment, modality, ESRD treatment status and claim categories are taken as inputs.

## Implementation notes
- "Past 12 months" for the limited life expectancy conditions is read as the reporting month plus the 11 months before it.
- An eligible patient, for the facility minimum, is one with at least one denominator month.

## Run
```
python3 ltc_rate_spec_test.py
```
Python 3.8+, standard library only. Prints the test table, the facility roll-up and "ALL CHECKS PASS".

Gaurav Bhatti, pharmacist by training, MHI (University of Michigan). bgaurav@umich.edu
