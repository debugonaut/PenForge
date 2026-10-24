-- Frozen 0001 invariants. Change through a new migration after this baseline ships.
CREATE FUNCTION penforge_unicode_scalars(value integer[]) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT COALESCE(array_ndims(value) = 1 AND array_lower(value, 1) = 1
    AND cardinality(value) BETWEEN 1 AND 16
    AND NOT EXISTS (SELECT 1 FROM unnest(value) cp
      WHERE cp IS NULL OR cp < 0 OR cp > 1114111 OR cp BETWEEN 55296 AND 57343), false)
$$;
