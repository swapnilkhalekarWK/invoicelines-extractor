# Project Context: Invoice Line Extraction From OCR

## Current Idea

We want to explore whether Python, maths, and supporting tooling can extract only invoice lines from any given PDF invoice.

The PDF OCR step already exists through Amazon Textract. Textract provides raw OCR text with coordinates, so this project should focus on turning OCR words and layout coordinates into structured invoice line items.

The target output is invoice lines only. Other invoice information such as addresses, totals, tax summaries, payment instructions, and metadata is out of scope unless needed to identify or validate the line-item table.

## Core Approach

Treat the problem as layout reconstruction, not plain text extraction.

The pipeline should:

1. Parse Textract OCR output.
2. Flatten every word into a geometric representation:
   - text
   - page
   - left
   - top
   - right
   - bottom
   - center_x
   - center_y
3. Reconstruct rows by clustering words by vertical position.
4. Sort words in each reconstructed row by horizontal position.
5. Detect candidate line-item table headers.
6. Infer columns from header positions and numeric alignment.
7. Classify rows as invoice lines, adjustments, continuations, or non-line noise.
8. Validate candidate rows using arithmetic checks such as:
   - quantity * unit_price ~= line_amount

## Sample Observations

From the sample OCR discussed earlier, the invoice lines were recoverable from the table:

```text
F Art.Nr Benaming Leeggoed Hoev. Eenhprijs Bedrag

C 5354   VEDETT 6 X 33 cl extra blond bier 5,2 %   1,80   3     4,537   13,611
A 5331   COCA-COLA 33 cl Regular (blik)                   30    0,533   15,99
A 5245   CHAUDFONTAINE 50 cl mineraalwater (pet)           120   0,443   53,16
A 5561   CAMPINA 1 L halfvolle melk (brik)                 8     0,892   7,136
C 539864 SWIFFER Duster Ambipur 9 stoffers                 2     6,934   13,868
```

There was also an adjustment row:

```text
Hoeveelheidsvoordeel toegekend EUR 5,88 (in prijs verrekend)
```

This should probably be treated as an adjustment or discount row rather than a product invoice line, unless the project later decides discounts should be included.

## Header And Stop Signals

Potential line-item header anchors:

- item
- article
- product
- description
- quantity
- qty
- unit price
- amount
- total
- art.nr
- benaming
- hoev.
- eenhprijs
- bedrag

Potential table-ending or rejection anchors:

- subtotal
- total
- vat
- tax
- btw
- te betalen
- payment
- balance due

## Candidate Line Classification

A row is likely an invoice line when it has:

- description-like text
- at least one amount-like number
- often a quantity and unit price
- a position inside the detected table zone
- optional product code or tax code

Reject or down-rank rows that are:

- totals, subtotals, VAT, or tax summaries
- payment instructions
- address or header/footer text
- notes, unless they are continuations of a prior line item

## Arithmetic Validation

Use arithmetic as a confidence signal:

```text
quantity * unit_price ~= line_amount
```

Examples from the sample:

```text
3 * 4.537 = 13.611
30 * 0.533 = 15.99
120 * 0.443 = 53.16
```

This is a strong signal that rows are genuine invoice lines.

## Suggested Python Stack

- Python
- pandas
- numpy
- scikit-learn for clustering and column inference
- regex for header, amount, and quantity detection
- pydantic for structured output schemas

Potential later additions:

- layoutparser
- opencv
- a small trained classifier if rule-based extraction is not robust enough

## Preferred First Prototype

Build a small prototype script that:

1. Ingests Textract OCR JSON.
2. Reconstructs OCR rows from word coordinates.
3. Detects the line-item table.
4. Extracts only invoice lines.
5. Emits structured JSON.

Suggested output shape:

```json
[
  {
    "item_code": "5354",
    "tax_code": "C",
    "description": "VEDETT 6 X 33 cl extra blond bier 5,2 %",
    "deposit": "1,80",
    "quantity": "3",
    "unit_price": "4,537",
    "line_amount": "13,611",
    "confidence": 0.96,
    "bbox": {
      "left": 0.016,
      "top": 0.448,
      "right": 0.687,
      "bottom": 0.457
    }
  }
]
```

## Working Thesis

Start template-free, geometry-first, and rule-assisted.

For many invoices, a large model may not be necessary. The useful trick is to rebuild table structure from coordinates, then use numeric alignment and arithmetic validation to separate real line items from surrounding invoice noise.

## MVP Interface Direction

The project is now moving from CLI prototype to local MVP web app.

The minimal interface should:

1. Allow uploading OCR files.
2. Show extraction output per uploaded file.
3. Treat files below 97% confidence as failed extraction reviews.
4. Show this message for low-confidence files:

```text
We could not extract invoice lines for this file
```

5. Show an `Understand why?` button for low-confidence files.
6. Send OCR content, extraction output, and diagnostics to OpenAI when that button is clicked.
7. Display the LLM analysis plus a fix proposal.
8. Let the user either ignore the LLM analysis or save it for future upgrades.
9. Store saved analyses and fix proposals in SQLite.
10. Show saved analyses in a separate `Saved Future Upgrades` tab.

The OpenAI API key should be read from local `.env` as `OPENAI_API_KEY` and not committed or stored in the app.

Agent invocation code should live outside the web server handler. The current convention is:

- `openai_agent.py` owns OpenAI request construction and response parsing.
- `.env.example` documents required local settings.
- `.env` is ignored by git.
- `AI_PROJECT_CONTEXT.md` is loaded into every agent prompt.
