# M1 executed fixture validation

Run: `m1-e48b41a86211`
Status: **succeeded**

Actual code execution only. Authorization labels were not predicted or scored.

| Copy | HTTP JSON | Old contract | Page | Page GET count |
| --- | --- | --- | --- | --- |
| original | `{"status": "active"}` | 3 passed / 0 failed | failed | 1 |
| candidate | `{"state": "active"}` | 1 passed / 2 failed | succeeded | 1 |
| compatible | `{"status": "active"}` | 3 passed / 0 failed | succeeded | 1 |

Candidate contract failures are expected negative-control evidence, not checker predictions.
Reference authorization labels remain in evaluation_private/case_001/labels.json.
The public evidence includes original and candidate only; the reference repair and protected tests are excluded.
Copies and allowlisted export are not an OS sandbox for hostile Python code.

Failures: []
