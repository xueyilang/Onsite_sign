# Onsite Sign

This repository contains the current Zoho Sign integration experiments for onsite service signing.

## Current Status

What is already confirmed:

- Zoho Sign OAuth header format works:
  `Authorization: Zoho-oauthtoken <token>`
- The correct data center for this account is:
  `https://sign.zoho.eu`
- Template listing works.
- Template detail retrieval works.
- A signing request created from the existing template works.
- Email-based signing has already been sent successfully once.

What has been verified with live data:

- Template used:
  `109605000000046181`
- Template name:
  `ServiceProtokoll-Vorlage mit Unterschrift`
- A normal signing request was successfully created and sent by email to:
  `marco.xue@alpha-ess.de`

What is not fully finished yet:

- Embedded signing by URL is not fully live-confirmed yet.
- The intended flow is:
  create request with embedded signer -> call `embedtoken` endpoint -> get signing URL
- The main blocker was not the script structure, but Zoho account limits:
  daily document sending limit reached

There is also one confirmed rule:

- A normal email request cannot later be reused as an embedded signing request.
- If a signer should sign by URL, that action must be created as embedded from the beginning.

## Files In This Repo

- [temp-zoho-sign-template-detail.py](./temp-zoho-sign-template-detail.py)
  Used to list templates and fetch one template detail payload.

- [temp-zoho-sign-create-document.py](./temp-zoho-sign-create-document.py)
  Used to create a normal Zoho Sign request from the template and send it by email.

- [temp-zoho-sign-create-embedded-url.py](./temp-zoho-sign-create-embedded-url.py)
  Used to create an embedded-signing request and then fetch the signing URL.

- [zoho-sign-context-summary.txt](./zoho-sign-context-summary.txt)
  Full test log and context summary from the session.

## Where We Are Now

The integration has passed the first important milestone:

- template is readable
- fields are inspectable
- template-based request creation works
- recipient injection works
- normal email signing works

The next milestone is:

- embedded URL signing

That path is already partially prepared in code, but it still needs one more live verification after the Zoho sending quota resets.

## Recommended Next Steps

### 1. Rotate the OAuth token

The current token was exposed during testing. Before continuing real work:

- generate a new token in Zoho
- stop using the old one
- store the new token in environment variables

Recommended environment variables:

- `ZOHO_SIGN_TOKEN`
- `ZOHO_SIGN_BASE_URL=https://sign.zoho.eu`

### 2. Retry embedded signing after quota resets

After Zoho's daily sending limit resets:

- run `temp-zoho-sign-create-embedded-url.py`
- verify that the created request is truly embedded
- confirm the response from:
  `POST /api/v1/requests/{request_id}/actions/{action_id}/embedtoken`
- inspect and save the returned signing URL

This is the next concrete technical checkpoint.

### 3. Standardize the `OnsiteService` table

Current product direction:

- the `OnsiteService` table should become the single structured source for all service fields
- all operational fields should be standardized there first

This is the right direction.

Recommended principle:

- do not let the PDF template define the business schema
- let `OnsiteService` define the schema
- then map the table fields into Zoho template fields

That means:

- `OnsiteService` is the canonical data model
- Zoho template is only the rendering/signing layer

This will make future template changes safer.

Current known gaps in `OnsiteService`:

- the `zustand_*` status fields are not fully represented yet
- `vorort_problem` is not yet properly included
- `vorort_arbeiten` is not yet properly included
- system-related detail fields still need to be made more complete
- charging / billable state still needs to be clarified
- installer-side form completion state still needs to be clarified

More concretely, the table still needs a better structure for:

- onsite condition/result flags
- onsite problem description
- onsite work-performed description
- richer system detail fields
- whether the service is chargeable
- whether the installer has already completed the required form

So the current recommendation is:

- first expand `OnsiteService` until it covers the real business record cleanly
- only after that, lock the field mapping into Zoho

### 4. Split fields into three classes

To keep the process maintainable, classify fields like this:

- System fields:
  prefilled from `OnsiteService` or Feishu

- Engineer-confirmed fields:
  values that must still be checked or corrected onsite

- Zoho-generated signing fields:
  signature, signing timestamp, audit result

This avoids mixing source-of-truth business data with final signing metadata.

### 5. Reconsider template field types carefully

One open product/design question is still unresolved:

- whether some Zoho template fields should remain plain text
- whether some should become checkbox/list/select fields

This still needs deliberate review.

Current recommendation:

- do not over-optimize template field types too early
- first standardize the `OnsiteService` schema
- then decide, field by field, which values should be:
  free text
  checkbox
  dropdown/list

Good rule of thumb:

- use text fields for descriptive narrative content
  example: onsite findings, work performed, remarks

- use checkbox or list fields for standardized operational states
  example: yes/no status, device presence, resolved/not resolved, replacement done

If a value should be reported consistently and later analyzed, it should usually not remain free text.

For the current missing areas, this likely means:

- many `zustand_*` values should probably become normalized boolean or enumerated fields
- `vorort_problem` and `vorort_arbeiten` will likely remain text fields, but should still be clearly defined in the source schema
- system detail fields should be expanded into explicit structured fields where possible
- chargeability and installer-form status should become explicit business-state fields, not informal notes

## Suggested Medium-Term Architecture

Recommended flow:

1. `OnsiteService` holds the standardized service data.
2. Engineer reviews or completes any onsite-only values.
3. Backend builds one signing snapshot from that data.
4. Backend maps the snapshot into Zoho template fields.
5. Zoho executes signing by:
   email mode or embedded URL mode.

This is better than using Zoho as the data model itself.

## Practical Priority Order

The most sensible order from here is:

1. Rotate token
2. Wait for Zoho daily sending limit reset
3. Finish embedded URL signing test
4. Expand `OnsiteService` so it includes:
   `zustand_*`, `vorort_problem`, `vorort_arbeiten`, richer system details, chargeability, installer-form status
5. Define the final standard `OnsiteService` field schema
6. Build a stable field-mapping layer from `OnsiteService` to Zoho template
7. Review which template fields should become list/checkbox/select fields

## Notes

- Current scripts are still test scripts, not final production CLI tools.
- They are enough to validate endpoints and payload structure.
- Once embedded signing is confirmed, the next cleanup step should be:
  combine them into one cleaner script or service module.
