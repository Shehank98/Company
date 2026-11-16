# Mail templates (defaults, please review)

These are in `agentco/templates.py`. They are filled **only** with verified booking data (names, references, amounts, dates, ad text). The model may only personalise the one greeting line, and that line is discarded unless it is a plain "Dear/Hello/Hi <first name>".
Tell me the changes you want and I will update them; nothing else about the wording is generated.

**Payment confirmation (customer)**
```
Dear Ann,

We have received your payment of Rs. 5,000.00 for booking BOOK-20260101-001 (quotation ADM-2026-1001).

Your ad:
- Daily News, classified ad, publish date 2026-12-01

We will now send your ad to the newspaper. You will get another email with the newspaper's receipt once it is issued.

Thank you for booking with AdSpot Media.
AdSpot Media - adspotmedia.lk
```
**Publication receipt (customer, receipt attached)**: "The newspaper has issued a receipt for your booking ... It is attached to this email." plus the ad lines.

**Publication request (to the newspaper, a DRAFT you edit)**: reference numbers, newspaper, ad type, publish date, size, the ad text, artwork link and file. Customer phone/email are NOT included. AdSpot's data has no "edition" field, so the mail says so.
