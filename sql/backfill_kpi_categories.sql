INSERT INTO kpi_categories (id, name, created_at)
SELECT
    gen_random_uuid(),
    category_name,
    NOW()
FROM (
    SELECT DISTINCT
        btrim(category_item->>'name') AS category_name
    FROM kpi_formulas
    CROSS JOIN LATERAL jsonb_array_elements(kpi_formulas.categories::jsonb) AS category_item
    WHERE category_item->>'name' IS NOT NULL
      AND btrim(category_item->>'name') <> ''
) AS formula_categories
WHERE NOT EXISTS (
    SELECT 1
    FROM kpi_categories existing_category
    WHERE lower(existing_category.name) = lower(category_name)
)
ON CONFLICT (name) DO NOTHING;
