-- =====================================================
-- ANALYZE DATABASE SIZE
-- =====================================================
-- Run this in Supabase SQL Editor to see the size of your tables.

SELECT
  table_name,
  pg_size_pretty(pg_total_relation_size(quote_ident(table_name))) as total_size,
  pg_size_pretty(pg_relation_size(quote_ident(table_name))) as data_size,
  pg_size_pretty(pg_total_relation_size(quote_ident(table_name)) - pg_relation_size(quote_ident(table_name))) as index_size
FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY pg_total_relation_size(quote_ident(table_name)) DESC;
