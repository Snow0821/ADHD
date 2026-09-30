-- Optional private PostgreSQL knowledge store (PostgreSQL 14+).
-- Review and apply explicitly through an authorized administrative connection.
-- This file does not configure credentials, expose a Data API, or grant access.
-- Calls run with the connector's existing privileges; no privileged RPC is added.
BEGIN;

-- Claim a fresh namespace atomically. Never adopt or alter an existing schema
-- based only on its name; the exact marker identifies this schema contract.
DO $schema$
DECLARE
    v_schema_oid oid;
BEGIN
    SELECT n.oid INTO v_schema_oid FROM pg_catalog.pg_namespace AS n
        WHERE n.nspname = 'adhd_knowledge';
    IF FOUND THEN
        IF pg_catalog.obj_description(v_schema_oid, 'pg_namespace')
           IS DISTINCT FROM 'adhd:knowledge-store:1' THEN
            RAISE EXCEPTION 'adhd_knowledge already exists without the expected ADHD schema marker; refusing to modify it'
                USING ERRCODE = '55000';
        END IF;
    ELSE
        CREATE SCHEMA adhd_knowledge;
        COMMENT ON SCHEMA adhd_knowledge IS 'adhd:knowledge-store:1';
    END IF;
END;
$schema$;

REVOKE ALL ON SCHEMA adhd_knowledge FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA adhd_knowledge
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

CREATE TABLE IF NOT EXISTS adhd_knowledge.graphs (
    graph_id text PRIMARY KEY CHECK (graph_id ~ '[^[:space:]]'),
    metadata jsonb NOT NULL CHECK (pg_catalog.jsonb_typeof(metadata) = 'object'),
    next_id bigint NOT NULL CHECK (next_id BETWEEN 0 AND 1000000),
    revision bigint NOT NULL CHECK (revision > 0),
    last_operation_id uuid NOT NULL,
    last_expected_revision bigint NOT NULL CHECK (last_expected_revision >= 0),
    last_payload_sha256 bytea NOT NULL,
    CHECK (revision = last_expected_revision + 1)
);

CREATE TABLE IF NOT EXISTS adhd_knowledge.nodes (
    graph_id text NOT NULL REFERENCES adhd_knowledge.graphs(graph_id),
    node_id text NOT NULL CHECK (node_id ~ '^k[0-9]{6}$'),
    position bigint NOT NULL CHECK (position > 0),
    metadata jsonb NOT NULL CHECK (pg_catalog.jsonb_typeof(metadata) = 'object'),
    body text NOT NULL,
    PRIMARY KEY (graph_id, node_id),
    UNIQUE (graph_id, position)
);

CREATE TABLE IF NOT EXISTS adhd_knowledge.edges (
    graph_id text NOT NULL REFERENCES adhd_knowledge.graphs(graph_id),
    position bigint NOT NULL CHECK (position > 0),
    source_id text NOT NULL,
    relation text NOT NULL CHECK (relation IN (
        'related_to', 'part_of', 'depends_on', 'supports',
        'contradicts', 'extends', 'supersedes'
    )),
    target_id text NOT NULL,
    reason text NOT NULL CHECK (reason ~ '[^[:space:]]'),
    -- Normalize identity, preserving the supplied orientation for lossless export.
    canonical_source_id text GENERATED ALWAYS AS (
        CASE WHEN relation IN ('related_to', 'contradicts') AND target_id < source_id
             THEN target_id ELSE source_id END
    ) STORED,
    canonical_target_id text GENERATED ALWAYS AS (
        CASE WHEN relation IN ('related_to', 'contradicts') AND target_id < source_id
             THEN source_id ELSE target_id END
    ) STORED,
    PRIMARY KEY (graph_id, position),
    UNIQUE (graph_id, canonical_source_id, relation, canonical_target_id),
    FOREIGN KEY (graph_id, source_id) REFERENCES adhd_knowledge.nodes(graph_id, node_id),
    FOREIGN KEY (graph_id, target_id) REFERENCES adhd_knowledge.nodes(graph_id, node_id),
    CHECK (source_id <> target_id)
);

CREATE INDEX IF NOT EXISTS edges_source_idx
    ON adhd_knowledge.edges (graph_id, source_id);
CREATE INDEX IF NOT EXISTS edges_target_idx
    ON adhd_knowledge.edges (graph_id, target_id);

ALTER TABLE adhd_knowledge.graphs ENABLE ROW LEVEL SECURITY;
ALTER TABLE adhd_knowledge.nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE adhd_knowledge.edges ENABLE ROW LEVEL SECURITY;
-- Intentionally no RLS policies: only the existing owner/admin connection is used.
-- Never add a permissive policy or SECURITY DEFINER to work around permissions.

CREATE OR REPLACE FUNCTION adhd_knowledge._string_array(p_value jsonb)
RETURNS boolean
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path = ''
AS $function$
    SELECT CASE WHEN pg_catalog.jsonb_typeof(p_value) = 'array' THEN
        NOT EXISTS (
            SELECT 1 FROM pg_catalog.jsonb_array_elements(p_value) AS item(value)
            WHERE pg_catalog.jsonb_typeof(item.value) IS DISTINCT FROM 'string'
        ) ELSE false END;
$function$;

CREATE OR REPLACE FUNCTION adhd_knowledge.validate_snapshot(p_snapshot jsonb)
RETURNS void
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path = ''
AS $function$
DECLARE
    v_graph jsonb;
    v_node jsonb;
    v_metadata jsonb;
    v_edge jsonb;
    v_line text;
    v_in_core boolean;
    v_has_core boolean;
    v_ids text[] := ARRAY[]::text[];
    v_seen_edges jsonb := '{}'::jsonb;
    v_edge_key text;
    v_source text;
    v_target text;
    v_relation text;
    v_next_id bigint;
    v_highest_id bigint := -1;
    v_previous_id text;
BEGIN
    IF pg_catalog.jsonb_typeof(p_snapshot) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'Snapshot must be a JSON object' USING ERRCODE = '22023';
    END IF;
    IF NOT (p_snapshot ?& ARRAY['graph', 'next_id', 'nodes', 'edges'])
       OR (p_snapshot - ARRAY['graph', 'next_id', 'nodes', 'edges']) <> '{}'::jsonb THEN
        RAISE EXCEPTION 'Snapshot fields must be graph, next_id, nodes, edges'
            USING ERRCODE = '22023';
    END IF;
    IF pg_catalog.jsonb_typeof(p_snapshot->'graph') IS DISTINCT FROM 'object'
       OR pg_catalog.jsonb_typeof(p_snapshot->'nodes') IS DISTINCT FROM 'array'
       OR pg_catalog.jsonb_typeof(p_snapshot->'edges') IS DISTINCT FROM 'array'
       OR pg_catalog.jsonb_typeof(p_snapshot->'next_id') IS DISTINCT FROM 'number'
       OR (p_snapshot->>'next_id') !~ '^(0|[1-9][0-9]*)$' THEN
        RAISE EXCEPTION 'Invalid snapshot field types or next_id' USING ERRCODE = '22023';
    END IF;
    -- Cast only after type validation; overflow also aborts the whole transaction.
    v_next_id := (p_snapshot->>'next_id')::bigint;
    IF v_next_id > 1000000 THEN
        RAISE EXCEPTION 'next_id exceeds the six-digit ID range' USING ERRCODE = '22023';
    END IF;
    v_graph := p_snapshot->'graph';
    IF v_graph->'schema_version' IS DISTINCT FROM '2'::jsonb
       OR v_graph->>'kind' IS DISTINCT FROM 'workspace'
       OR pg_catalog.jsonb_typeof(v_graph->'graph_id') IS DISTINCT FROM 'string'
       OR (v_graph->>'graph_id') !~ '[^[:space:]]'
       OR pg_catalog.jsonb_typeof(v_graph->'name') IS DISTINCT FROM 'string'
       OR (v_graph->>'name') !~ '[^[:space:]]'
       OR pg_catalog.jsonb_typeof(v_graph->'default_scope') IS DISTINCT FROM 'string'
       OR (v_graph->>'default_scope') !~ '^(common|project:[a-z0-9][a-z0-9-]*)$'
       OR NOT adhd_knowledge._string_array(v_graph->'imports') THEN
        RAISE EXCEPTION 'Invalid schema-2 graph metadata' USING ERRCODE = '22023';
    END IF;

    FOR v_node IN SELECT value FROM pg_catalog.jsonb_array_elements(p_snapshot->'nodes') LOOP
        IF pg_catalog.jsonb_typeof(v_node) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'Each node must be an object' USING ERRCODE = '22023';
        END IF;
        IF NOT (v_node ?& ARRAY['id', 'metadata', 'body'])
           OR (v_node - ARRAY['id', 'metadata', 'body']) <> '{}'::jsonb
           OR pg_catalog.jsonb_typeof(v_node->'id') IS DISTINCT FROM 'string'
           OR (v_node->>'id') !~ '^k[0-9]{6}$'
           OR pg_catalog.jsonb_typeof(v_node->'metadata') IS DISTINCT FROM 'object'
           OR pg_catalog.jsonb_typeof(v_node->'body') IS DISTINCT FROM 'string' THEN
            RAISE EXCEPTION 'Invalid node fields or types' USING ERRCODE = '22023';
        END IF;
        IF (v_node->>'id') = ANY(v_ids) THEN
            RAISE EXCEPTION 'Duplicate node ID' USING ERRCODE = '22023';
        END IF;
        IF v_previous_id IS NOT NULL AND (v_node->>'id') < v_previous_id THEN
            RAISE EXCEPTION 'Snapshot nodes must be sorted by ID' USING ERRCODE = '22023';
        END IF;
        v_previous_id := v_node->>'id';
        v_ids := pg_catalog.array_append(v_ids, v_node->>'id');
        v_highest_id := GREATEST(v_highest_id, pg_catalog.substr(v_node->>'id', 2)::bigint);
        v_metadata := v_node->'metadata';
        IF pg_catalog.jsonb_typeof(v_metadata->'title') IS DISTINCT FROM 'string'
           OR (v_metadata->>'title') !~ '[^[:space:]]'
           OR pg_catalog.jsonb_typeof(v_metadata->'kind') IS DISTINCT FROM 'string'
           OR (v_metadata->>'kind') NOT IN ('concept', 'claim', 'idea', 'question', 'source')
           OR pg_catalog.jsonb_typeof(v_metadata->'status') IS DISTINCT FROM 'string'
           OR (v_metadata->>'status') NOT IN ('active', 'superseded', 'archived')
           OR pg_catalog.jsonb_typeof(v_metadata->'scope') IS DISTINCT FROM 'string'
           OR (v_metadata->>'scope') !~ '^(common|project:[a-z0-9][a-z0-9-]*)$'
           OR NOT adhd_knowledge._string_array(v_metadata->'aliases')
           OR NOT adhd_knowledge._string_array(v_metadata->'sources') THEN
            RAISE EXCEPTION 'Invalid node metadata' USING ERRCODE = '22023';
        END IF;
        -- Read the first core section without modifying literal Markdown content.
        v_in_core := false;
        v_has_core := false;
        FOREACH v_line IN ARRAY pg_catalog.string_to_array(v_node->>'body', E'\n') LOOP
            IF v_in_core THEN
                EXIT WHEN v_line ~ '^##[[:space:]]';
                IF v_line ~ '[^[:space:]]' THEN
                    v_has_core := true;
                    EXIT;
                END IF;
            ELSIF v_line ~ '^## 핵심[[:space:]]*$' THEN
                v_in_core := true;
            END IF;
        END LOOP;
        IF NOT v_has_core THEN
            RAISE EXCEPTION 'Node body must have a nonempty 핵심 section' USING ERRCODE = '22023';
        END IF;
    END LOOP;
    IF v_next_id <= v_highest_id THEN
        RAISE EXCEPTION 'next_id must exceed every node ID' USING ERRCODE = '22023';
    END IF;

    FOR v_edge IN SELECT value FROM pg_catalog.jsonb_array_elements(p_snapshot->'edges') LOOP
        IF pg_catalog.jsonb_typeof(v_edge) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'Each edge must be an object' USING ERRCODE = '22023';
        END IF;
        IF NOT (v_edge ?& ARRAY['from', 'relation', 'to', 'reason'])
           OR (v_edge - ARRAY['from', 'relation', 'to', 'reason']) <> '{}'::jsonb
           OR pg_catalog.jsonb_typeof(v_edge->'from') IS DISTINCT FROM 'string'
           OR pg_catalog.jsonb_typeof(v_edge->'to') IS DISTINCT FROM 'string'
           OR pg_catalog.jsonb_typeof(v_edge->'relation') IS DISTINCT FROM 'string'
           OR pg_catalog.jsonb_typeof(v_edge->'reason') IS DISTINCT FROM 'string'
           OR (v_edge->>'reason') !~ '[^[:space:]]' THEN
            RAISE EXCEPTION 'Invalid edge fields or types' USING ERRCODE = '22023';
        END IF;
        v_source := v_edge->>'from';
        v_target := v_edge->>'to';
        v_relation := v_edge->>'relation';
        IF NOT (v_source = ANY(v_ids)) OR NOT (v_target = ANY(v_ids))
           OR v_source = v_target
           OR v_relation NOT IN ('related_to', 'part_of', 'depends_on', 'supports',
                                 'contradicts', 'extends', 'supersedes') THEN
            RAISE EXCEPTION 'Edge has missing/scoped endpoints, a self-link, or invalid relation'
                USING ERRCODE = '22023';
        END IF;
        IF v_relation IN ('related_to', 'contradicts') AND v_target < v_source THEN
            v_edge_key := pg_catalog.jsonb_build_array(v_target, v_relation, v_source)::text;
        ELSE
            v_edge_key := pg_catalog.jsonb_build_array(v_source, v_relation, v_target)::text;
        END IF;
        IF v_seen_edges ? v_edge_key THEN
            RAISE EXCEPTION 'Duplicate edge after symmetric normalization' USING ERRCODE = '22023';
        END IF;
        v_seen_edges := v_seen_edges || pg_catalog.jsonb_build_object(v_edge_key, true);
    END LOOP;
END;
$function$;

CREATE OR REPLACE FUNCTION adhd_knowledge.export_graph(p_graph_id text)
RETURNS jsonb
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = ''
AS $function$
    -- One statement/snapshot prevents a torn read across graph, nodes, and edges.
    -- A missing graph returns SQL NULL; revision zero is reserved for creation.
    SELECT pg_catalog.jsonb_build_object(
        'graph_id', g.graph_id, 'revision', g.revision,
        'operation_id', g.last_operation_id,
        'snapshot', pg_catalog.jsonb_build_object(
            'graph', g.metadata, 'next_id', g.next_id,
            'nodes', COALESCE((
                SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                    'id', n.node_id, 'metadata', n.metadata, 'body', n.body
                ) ORDER BY n.position)
                FROM adhd_knowledge.nodes AS n WHERE n.graph_id = g.graph_id
            ), '[]'::jsonb),
            'edges', COALESCE((
                SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
                    'from', e.source_id, 'relation', e.relation,
                    'to', e.target_id, 'reason', e.reason
                ) ORDER BY e.position)
                FROM adhd_knowledge.edges AS e WHERE e.graph_id = g.graph_id
            ), '[]'::jsonb)
        )
    ) FROM adhd_knowledge.graphs AS g WHERE g.graph_id = p_graph_id;
$function$;

CREATE OR REPLACE FUNCTION adhd_knowledge.replace_graph(
    p_graph_id text,
    p_expected_revision bigint,
    p_operation_id uuid,
    p_snapshot jsonb
)
RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
DECLARE
    v_current adhd_knowledge.graphs%ROWTYPE;
    v_exists boolean;
    v_revision bigint;
    v_payload_sha256 bytea;
BEGIN
    IF p_graph_id IS NULL OR p_graph_id !~ '[^[:space:]]'
       OR p_expected_revision IS NULL OR p_expected_revision < 0
       OR p_operation_id IS NULL THEN
        RAISE EXCEPTION 'graph_id, nonnegative expected revision, and operation UUID are required'
            USING ERRCODE = '22023';
    END IF;
    PERFORM adhd_knowledge.validate_snapshot(p_snapshot);
    v_payload_sha256 := pg_catalog.sha256(pg_catalog.convert_to(p_snapshot::text, 'UTF8'));
    -- The advisory lock also serializes two creators before a graph row exists.
    -- Hash collisions only serialize unrelated graphs; they never mix their data.
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended('adhd_knowledge:' || p_graph_id, 0)
    );
    SELECT * INTO v_current FROM adhd_knowledge.graphs AS g
        WHERE g.graph_id = p_graph_id FOR UPDATE;
    v_exists := FOUND;

    IF v_exists AND v_current.last_operation_id = p_operation_id THEN
        IF v_current.last_expected_revision <> p_expected_revision
           OR v_current.last_payload_sha256 <> v_payload_sha256 THEN
            RAISE EXCEPTION 'Operation UUID reused with different request content'
                USING ERRCODE = '40001';
        END IF;
        RETURN pg_catalog.jsonb_build_object(
            'graph_id', p_graph_id, 'revision', v_current.revision,
            'operation_id', p_operation_id, 'replayed', true
        );
    END IF;
    IF (v_exists AND v_current.revision <> p_expected_revision)
       OR (NOT v_exists AND p_expected_revision <> 0) THEN
        RAISE EXCEPTION 'Graph revision conflict; export the current graph before retrying'
            USING ERRCODE = '40001';
    END IF;
    IF v_exists AND (p_snapshot->>'next_id')::bigint < v_current.next_id THEN
        RAISE EXCEPTION 'next_id cannot move backwards' USING ERRCODE = '22023';
    END IF;
    IF v_exists AND EXISTS (
        SELECT 1 FROM adhd_knowledge.nodes AS existing
        WHERE existing.graph_id = p_graph_id AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.jsonb_array_elements(p_snapshot->'nodes') AS incoming(value)
            WHERE incoming.value->>'id' = existing.node_id
        )
    ) THEN
        RAISE EXCEPTION 'Existing node IDs must be preserved; archive or supersede nodes instead'
            USING ERRCODE = '22023';
    END IF;
    v_revision := p_expected_revision + 1;
    IF v_exists THEN
        -- No changes are visible until the surrounding transaction commits. Any
        -- validation, constraint, or connection failure rolls back the entire call.
        DELETE FROM adhd_knowledge.edges WHERE graph_id = p_graph_id;
        DELETE FROM adhd_knowledge.nodes WHERE graph_id = p_graph_id;
        UPDATE adhd_knowledge.graphs SET
            metadata = p_snapshot->'graph', next_id = (p_snapshot->>'next_id')::bigint,
            revision = v_revision, last_operation_id = p_operation_id,
            last_expected_revision = p_expected_revision, last_payload_sha256 = v_payload_sha256
        WHERE graph_id = p_graph_id;
    ELSE
        INSERT INTO adhd_knowledge.graphs (
            graph_id, metadata, next_id, revision,
            last_operation_id, last_expected_revision, last_payload_sha256
        ) VALUES (
            p_graph_id, p_snapshot->'graph', (p_snapshot->>'next_id')::bigint, v_revision,
            p_operation_id, p_expected_revision, v_payload_sha256
        );
    END IF;
    INSERT INTO adhd_knowledge.nodes (graph_id, node_id, position, metadata, body)
        SELECT p_graph_id, item.value->>'id', item.ordinality,
               item.value->'metadata', item.value->>'body'
        FROM pg_catalog.jsonb_array_elements(p_snapshot->'nodes')
             WITH ORDINALITY AS item(value, ordinality);
    INSERT INTO adhd_knowledge.edges (graph_id, position, source_id, relation, target_id, reason)
        SELECT p_graph_id, item.ordinality, item.value->>'from', item.value->>'relation',
               item.value->>'to', item.value->>'reason'
        FROM pg_catalog.jsonb_array_elements(p_snapshot->'edges')
             WITH ORDINALITY AS item(value, ordinality);
    RETURN pg_catalog.jsonb_build_object(
        'graph_id', p_graph_id, 'revision', v_revision,
        'operation_id', p_operation_id, 'replayed', false
    );
END;
$function$;

REVOKE ALL ON ALL TABLES IN SCHEMA adhd_knowledge FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA adhd_knowledge FROM PUBLIC;
-- Supabase roles are optional: this script also works on ordinary PostgreSQL.
DO $permissions$
DECLARE
    v_role text;
BEGIN
    FOREACH v_role IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = v_role) THEN
            EXECUTE pg_catalog.format('REVOKE ALL ON SCHEMA adhd_knowledge FROM %I', v_role);
            EXECUTE pg_catalog.format('REVOKE ALL ON ALL TABLES IN SCHEMA adhd_knowledge FROM %I', v_role);
            EXECUTE pg_catalog.format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA adhd_knowledge FROM %I', v_role);
            EXECUTE pg_catalog.format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA adhd_knowledge REVOKE ALL ON TABLES FROM %I', v_role
            );
            EXECUTE pg_catalog.format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA adhd_knowledge REVOKE EXECUTE ON FUNCTIONS FROM %I', v_role
            );
        END IF;
    END LOOP;
END;
$permissions$;

COMMIT;
