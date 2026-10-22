# Jinja-to-Nunjucks template audit

The 29 converted `.njk` templates contain these template constructs:

| Construct | Occurrences |
|---|---:|
| `extends` | 24 |
| `include` | 5 |
| `block` / `endblock` | 74 / 74 |
| `set` | 27 |
| `if` / `elif` / `else` / `endif` | 46 / 1 / 32 / 46 |
| `for` / `endfor` | 3 / 3 |
| Variable expressions | 47 |

Filters in use are `default`, `first`, `items`, `length`, `strftime`, `tojson`,
and `upper`. Autoescape is enabled in the Node environment.

## Python-specific expression conversions

- Mapping `.get(key, default)` expressions were converted to indexed access
  with an `is defined` conditional when the original expression supplied a
  default. No JavaScript `get()` calls inside `<script>` blocks were changed.
- `value.strftime('%Y-%m-%d')` was converted to
  `value|strftime('%Y-%m-%d')`; the Node filter formats the date without
  shifting the calendar date for ISO strings.
- `value[:1]|upper` was converted to `value|first|upper`.
- `permission_map.items()` was converted to `permission_map|items`, with a
  Nunjucks filter returning the mapping's entries.
- Python tuple pairs in the profile's loop were converted to nested Nunjucks
  arrays.
- Template inheritance and include paths now use `.njk`; other constructs
  (`set`, blocks, `extends`, `include`, `or`, and inline conditionals) remain
  supported without changes.

The converter verified the original text outside template delimiters remained
byte-for-byte identical. The later compatibility edits above only change
expressions inside the existing `{% ... %}` tags.
