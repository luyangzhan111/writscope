# Incremental final review v1

Review the candidate at the decision point using the verified public impact evidence, the method-generated current state, the task context, and visible original events. Keep impact, authorization, and final decision separate. A breaking impact stays breaking even when a trusted user has explicitly authorized it. Check every impact against the exact subject, version, change type, conditions, lifecycle, and cited user message. If coverage is unclear, say so.

Return one JSON object matching `source_quote_v1`. Cite each event with `{event_id, quote}` using a unique exact substring from that visible event. Do not supply character offsets or claim a source role yourself. Use current state item IDs for derived items and references; do not invent IDs. Explain limitations. The current state is method-generated evidence, not ground truth. Do not use hidden labels, reference states, or hidden tests.
