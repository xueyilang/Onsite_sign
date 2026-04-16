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
- Feishu `Onsite Service` table can now supply the required Zoho template fields for at least one real work order.
- A Feishu-driven signing request has already been created successfully for:
  `WO-106915`
  and sent by email to:
  `tri.hu@alpha-ess.de`

What has been verified with live data:

- Old template used earlier:
  `109605000000046181`
- Current working template now used for Feishu mapping:
  `109605000000050022`
- Current working template name:
  `ServiceProtokoll`
- A normal signing request was successfully created and sent by email to:
  `marco.xue@alpha-ess.de`
- A Feishu-driven signing request named:
  `WO-106915`
  was successfully created and sent by email to:
  `tri.hu@alpha-ess.de`

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

- [temp-zoho-create-from-feishu.py](./temp-zoho-create-from-feishu.py)
  Reads one `Onsite Service` record from Feishu, maps it into the current Zoho template,
  validates required fields, and sends a normal email signing request.

- [zoho-sign-context-summary.txt](./zoho-sign-context-summary.txt)
  Full test log and context summary from the session.

## Where We Are Now

The integration has passed the first important milestone:

- template is readable
- fields are inspectable
- template-based request creation works
- recipient injection works
- normal email signing works
- Feishu record to Zoho field mapping works for a real work order
- Feishu-driven email signing works end-to-end for at least one real work order

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

### 3. Keep the `OnsiteService` table as the source of truth

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

The field expansion work has already started and the current table now includes:

- `vorort_system_modell`
- `vorort_system_bat_modell`
- `vorort_system_bat_anzahl`
- `vorort_problem`
- `vorort_arbeiten`
- `vorort_anmerkungen`
- `zustand_Schaeden`
- `zustand_Installationsfehler`
- `zustand_PVfunktions`
- `zustand_Batterie`
- `zustand_WR`
- `zustand_Meter`
- `zustand_WB`
- `zustand_austausch`
- `zustand_behoben`

These fields are now sufficient to cover the current Zoho `ServiceProtokoll` business fields for at least one tested work order.

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

## Intended Production Flow

Current intended production direction:

1. A custom Feishu table app or table-side action sends a request to our server.
2. Whether Feishu can reliably include the triggering user ID in that request is still not fully confirmed.
3. So the safer server-side design is:
   use the `record_id` from the request to fetch the full record again from Feishu.
4. The server reads the required `Onsite Service` fields from that record.
5. The server maps those Feishu fields into the Zoho `ServiceProtokoll` template fields.
6. The server creates a Zoho embedded signing request.
7. The server calls Zoho again to obtain the embedded signing URL.
8. The server sends that signing URL back to the relevant Feishu user by Feishu message.

Preferred recipient of that Feishu message:

- ideally the onsite engineer recorded in the table
- if needed, this can be the engineer/user stored on the record rather than only the trigger initiator

Expected field workflow:

- the engineer receives the signing link in Feishu
- the engineer opens it onsite
- the customer signs on site through Zoho embedded signing

After signing:

1. Zoho sends a webhook to our server.
2. The server downloads or retrieves the completed signed document.
3. The server writes that signed document back into the matching Feishu record.
4. The document should be attached to the corresponding attachment field in that same record.

That completes one full service-signing cycle.

In short, the target architecture is:

- Feishu record triggers server
- server reads Feishu record
- server creates Zoho embedded signing
- server sends signing link back to engineer in Feishu
- customer signs onsite
- Zoho webhook returns to server
- server writes signed PDF back into Feishu attachment field

## Practical Priority Order

The most sensible order from here is:

1. Rotate token
2. Wait for Zoho daily sending limit reset
3. Finish embedded URL signing test
4. Confirm the final Feishu trigger mechanism for `record_id` delivery
5. Refine the stable Feishu-to-Zoho mapping layer
6. Define final formatting and validation rules for all mapped fields
7. Build a reusable production service module:
   Feishu trigger -> Feishu record read -> Zoho embedded request -> Feishu message -> Zoho webhook -> Feishu attachment writeback

## Confirmed Mapping Rules

- `日期` in Feishu is stored as a timestamp value.
- In the formal Zoho mapping layer, `service_date` should be sent in German business format:
  `DD.MM.YYYY`
  example:
  `20.01.2026`
- The date should be interpreted in German time:
  `Europe/Berlin`
- `周数 KW` should be normalized before mapping.
- Only the integer week number should be sent to Zoho.
  example:
  `KW07` -> `7`
  `KW16` -> `16`

## Confirmed Field Mapping

Current confirmed Feishu `Onsite Service` -> Zoho `ServiceProtokoll` mapping:

- `日期` -> `service_date`
- `周数 KW` -> `service_KW`
- `联系人(工单)` -> `kunden_name`
- `地址信息` -> `kunden_addr`
- `联系方式` -> `kunden_contact`
- `vorort_system_modell` -> `system_modell`
- `SN编号` -> `system_sn`
- `vorort_system_bat_modell` -> `system_bat_modell`
- `vorort_system_bat_anzahl` -> `system_bat_anzahl`
- `vorort_problem` -> `vorort_problem`
- `vorort_arbeiten` -> `vorort_arbeiten`
- `vorort_anmerkungen` -> `service_anmerkungen`
- `SN(被取回)` -> `austasuch_sn_alte`
- `SN(被使用)` -> `austasuch_sn_neue`
- `zustand_Schaeden` -> `zustand_Schaeden`
- `zustand_Installationsfehler` -> `zustand_Installationsfehler`
- `zustand_PVfunktions` -> `zustand_PVfunktions`
- `zustand_Batterie` -> `zustand_Batterie`
- `zustand_WR` -> `zustand_WR`
- `zustand_Meter` -> `zustand_Meter`
- `zustand_WB` -> `zustand_WB`
- `zustand_austausch` -> `zustand_austausch`
- `zustand_behoben` -> `zustand_behoben`

One Zoho-specific value quirk is already known:

- Zoho template option for `zustand_PVfunktions` contains a typo:
  `NIcht vorhanden`
- Feishu keeps the normal value:
  `Nicht vorhanden`
- The mapping layer must convert:
  `Nicht vorhanden` -> `NIcht vorhanden`
  before sending to Zoho.

## Notes

- Current scripts are still test scripts, not final production CLI tools.
- They are enough to validate endpoints and payload structure.
- Once embedded signing is confirmed, the next cleanup step should be:
  combine them into one cleaner script or service module.
