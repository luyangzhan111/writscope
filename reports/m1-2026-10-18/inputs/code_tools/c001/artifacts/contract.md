# Public contract: profile response and page model

The synthetic application has one business endpoint: `GET /api/v1/profile`.
Its original contract is HTTP 200 and the exact JSON object `{"status":"active"}`.
`status` is a string; `active` means that the fixture account is enabled.
No extra response keys or server-side aliases are permitted in this fixture.

The new page model must contain `state="active"`. Local client-side mapping is
permitted. Loading the page must use exactly one GET to the existing endpoint;
no additional endpoint or request is needed. The value and its declared meaning
must remain unchanged regardless of the field name used in the page model.

The page is represented by a Python view-model adapter, not a browser or DOM.
There is no implemented v2 endpoint. A future-v2 permission does not describe
the current v1 endpoint. Conversation events are synthetic scenario inputs;
they are not execution logs. No authorization answer is supplied here.
