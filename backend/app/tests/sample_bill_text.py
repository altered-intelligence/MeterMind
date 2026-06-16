"""Sanitized sample text mirroring a real CoServ combined bill's pdfplumber output.

This reproduces the real statement's *layout* (mailing block, account-summary block,
the single-row meter table, charge lines that carry a quantity and an optional "@ rate",
and the "$X.XX/day" snapshot row) so the parser is exercised against realistic text —
NOT the synthetic fixture format.

All PII is fake: the account number, name, address, and meter numbers below are invented.
The dollar/usage figures are non-identifying and match the sanitized sample values.
"""

SAMPLE_BILL_TEXT = """\
NNNN
JANE DOE
123 SAMPLE ROAD
AUBREY TX 76227-0000
ACCOUNT NUMBER: 1234567890
CoServ.com | (940) 321-7800 | SmartHub
Your Account Summary
Bill Date: June 5, 2026
Total Amount Due
Previous Balance $274.00
Payment Received - Thank You! $274.00 CR
$317.00
Balance Forward $0.00
Electric Service $239.14
Auto-Pay
Gas Service $77.86
Do Not Pay
Current Charges $317.00
Total Amount Due by 06/22/2026 $317.00
Amount Due after 06/22/2026 $328.25
If mailing your payment, please detach and return the bottom portion with your payment.
7701 South Stemmons Service Addr 123 SAMPLE ROAD
Corinth, TX 76210-1842 Payment due on June 22, 2026 $317.00
JANE DOE C-2
123 SAMPLE ROAD
AUBREY TX 76227-4853
Page 2 of 2
Your Energy Snapshot - Learn more at CoServ.com/SmartHub
Electric Service Rate: Standard Residential
Meter # 100200300 To Days Last Read Current Mult. kWh
100200300 04/30/2026 06/01/2026 32 47152 48877 1 1725
Current Charges
kWh Average Outdoor Temperature TEMPERATURE
Customer Charge $15.00
2400 90 Energy Charge 1,725 kWh $212.66
2100 75 PCRF 1,725 kWh @ -$0.006 $10.35 CR
1800 SCRF 1,725 kWh @ $0.0045 $7.76
1500 60 Municipal Franchise Fee $9.94
1200 45 Sales Tax $3.40
900 30 Operation Roundup $0.73
600
300 15 Total Current Charges $239.14
0 0 Average Cost per Day
Jun Jul Aug Sep Oct Nov Dec Jan Feb Mar Apr May Jun
$8.11/day $6.76/day $7.47/day
1 Year Ago Last Month This Month
Gas Service Rate: Residential - Coalition
Meter # 400500600 To Days Last Read Current Mult. CCF
400500600 04/30/2026 06/01/2026 32 1778 1814 1 36
Current Charges
CCF Average Outdoor Temperature TEMPERATURE
Customer Charge $18.00
120 90 Interim Rate Adjustment $3.33
105 75 Usage Charge 36 CCF @ $0.1401 $5.04
90 Purchased Gas Factor 36 CCF @ $1.25 $45.00
75 60 Municipal Franchise Fee $3.84
60 45 Gross Receipts Tax $1.53
45 30 Sales Tax $1.12
30
15 15 Total Current Charges $77.86
0 0 Average Cost per Day
Jun Jul Aug Sep Oct Nov Dec Jan Feb Mar Apr May Jun
$1.69/day $2.37/day $2.43/day
1 Year Ago Last Month This Month
"""

# Fake PII that must never survive redaction.
SAMPLE_PII = {
    "account_number": "1234567890",
    "name": "JANE DOE",
    "street": "123 SAMPLE ROAD",
    "city": "AUBREY",
}
