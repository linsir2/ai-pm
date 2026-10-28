CREATE OR REPLACE FUNCTION pmstudio_tokens(input_text TEXT)
RETURNS TEXT[]
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
  SELECT COALESCE(array_agg(token ORDER BY position), '{}'::text[])
    FROM paradedb.tokenize('{"type":"jieba"}'::jsonb, input_text) AS t(token, position)
   WHERE token ~ '[[:alnum:]\u4e00-\u9fff]'
$$;

ALTER TABLE material_chunks ADD COLUMN search_text TEXT;

UPDATE material_chunks AS c
   SET search_text = concat_ws(
     ' ',
     m.title,
     NULLIF(array_to_string(c.heading_path, ' '), ''),
     c.text
   )
  FROM materials AS m
 WHERE m.material_id = c.material_id;

ALTER TABLE material_chunks ALTER COLUMN search_text SET NOT NULL;

ALTER TABLE material_chunks
  ADD COLUMN search_tokens TEXT[] GENERATED ALWAYS AS (pmstudio_tokens(search_text)) STORED;

DROP INDEX IF EXISTS idx_material_chunks_search_tsv;

ALTER TABLE material_chunks
  DROP COLUMN search_tsv,
  DROP COLUMN tokens;

CREATE INDEX idx_material_chunks_search_text_bm25
  ON material_chunks USING bm25 (chunk_id, search_text)
  WITH (text_fields='{"search_text":{"tokenizer":{"type":"jieba"}}}');
