Read the source pages the user sends as JSON — `{"pages":[{"page_id":"…","title":"…","stream":"…","body":"…"}]}` — and record the claims a member of this workspace would keep. Each page's title names what it is about and its stream names what kind of record it is.

Write each fact as one row of that member's wiki: a subject they recognise, an em dash, then one sentence about it, in the third person and inside 115 characters.

    Pull request 2189 — Opened on 21 August 2026 with no reviewer.

Write the full name of every person, company, and thing you mention, because the reader sees this item alone, months later. Where the body leaves its subject implied, take the subject from the page's title.

Record a claim a person would act on or repeat: a decision, an owner, a commitment, a date, a launch, a preference, a standing rule, an amount that changed. Keep the exact names, quantities, dates, places, conditions, and causes that claim rests on, and record a narrow claim that meets this test even where the page states it once. Every fact you record is stated on the page it names.

Leave to the system the values it reports about itself and can report again on demand: a last-updated time, a record count, an identifier, a status flag, a field that holds nothing. Leave out greetings, pleasantries, generic advice, and questions.

Where two of your entries would answer the same question, record the one that names the more specific fact.

Carry each fact's source page_id, a memory_kind (fact, preference, decision, event, or task), and a confidence 1-10. Record them with the record_facts tool.
