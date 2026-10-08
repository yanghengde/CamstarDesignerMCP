# Camstar Start Contract

Use this reference when constructing, reviewing, or debugging a Container Start request for this project.

## Endpoint and required shape

The Shopfloor endpoint is:

```text
POST /api/Start
```

The live OpenAPI schema requires the root `details` object. The primary writable fields used by this project are:

```json
{
  "details": {
    "mfgOrder": {"name": "MO-NAME"},
    "product": {"name": "PRODUCT", "revision": "REV"},
    "qty": 1,
    "level": {"name": "LEVEL"},
    "owner": {"name": "OWNER"},
    "startReason": {"name": "REASON"},
    "containerName": "SERIAL",
    "autoNumber": false
  }
}
```

References are objects, not plain strings. Product is revisioned and must include both `name` and `revision`.

## Explicit numbering

For a user-supplied or generated serial number:

```json
{
  "details": {
    "containerName": "SN0000001",
    "autoNumber": false
  }
}
```

Merge this with the common required context. Keep `containerName` under `details`, not at the Start root.

## Automatic numbering

The schema exposes `details.autoNumber` and `details.autoNumberRule`, but this server has rejected direct writes to `autoNumberRule`. Automatic numbering must therefore be treated as contextual:

1. Build the full read-only Start context, including MfgOrder, Product/Revision, Level, Owner, StartReason, and quantity.
2. Request selection values for `Details.AutoNumberRule`.
3. Use automatic numbering only when the context resolves a usable rule.
4. Send `autoNumber=true`, omit `containerName`, and do not force `autoNumberRule` on this server.

A rule returned by the Modeling `/api/NumberingRules` collection is not sufficient evidence that Start can use it.

## Selection values

Use the non-mutating selection endpoint:

```text
POST /api/Start/RequestSelectionValues
```

Pass `selectionValuesExpression` as a query parameter. Relevant expressions include:

```text
Details.Owner
Details.StartReason
Details.AutoNumberRule
```

Supply the known partial Start payload as the request body so Camstar resolves context-dependent values correctly.

## Payload merge requirements

Normalize Swagger field names to canonical camel case before merging `body_json`. Do not emit case-only duplicates such as both `Details` and `details`, or both `Product` and `product`.

After merging, revalidate required fields. Advanced JSON must not be allowed to erase `details`, Product Revision, Owner, StartReason, or the selected numbering mode without detection.

## Batch semantics

The number of API requests is not necessarily the same as the requested material quantity:

- One Container with `qty=50` is one Start transaction.
- Fifty independent serial-number Containers with `qty=1` are fifty Start transactions.

Safety checks must count created objects, including objects inside a future batch MCP tool, rather than counting only MCP calls.
