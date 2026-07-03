"""Canonical schema. One home: chgraph-architecture-contract Decision 5 (nodes/edges)
and chgraph-git-evolution-campaign Phase 1 (git side tables). Changes -> chgraph-change-control."""

SCHEMA_VERSION = 1

DDL = [
    "CREATE DATABASE IF NOT EXISTS chgraph",
    """CREATE TABLE IF NOT EXISTS chgraph.nodes (
        project String,
        label LowCardinality(String),
        name String,
        qualified_name String,
        file_path String,
        start_line UInt32,
        end_line UInt32,
        properties String,
        version UInt64
    ) ENGINE = ReplacingMergeTree(version)
    ORDER BY (project, qualified_name)""",
    """CREATE TABLE IF NOT EXISTS chgraph.edges (
        project String,
        source String,
        target String,
        type LowCardinality(String),
        properties String,
        version UInt64
    ) ENGINE = ReplacingMergeTree(version)
    ORDER BY (project, type, source, target)""",
    """CREATE TABLE IF NOT EXISTS chgraph.git_commits (
        project String, hash FixedString(40), author_name String, author_email String,
        committed_at DateTime, message String
    ) ENGINE = MergeTree ORDER BY (project, committed_at, hash)""",
    """CREATE TABLE IF NOT EXISTS chgraph.git_file_changes (
        project String, hash FixedString(40), committed_at DateTime, author_email String,
        path String, old_path String, additions UInt32, deletions UInt32, is_rename UInt8
    ) ENGINE = MergeTree ORDER BY (project, path, committed_at)""",
    """CREATE TABLE IF NOT EXISTS chgraph.file_evolution (
        project String, path String,
        commit_count UInt32, churn UInt64,
        last_commit_at DateTime, top_author String, top_author_share Float32,
        recency_score Float32,
        version UInt64
    ) ENGINE = ReplacingMergeTree(version) ORDER BY (project, path)""",
    """CREATE TABLE IF NOT EXISTS chgraph.embeddings (
        project String, qualified_name String, vec Array(Float32), version UInt64
    ) ENGINE = ReplacingMergeTree(version) ORDER BY (project, qualified_name)""",
    """CREATE TABLE IF NOT EXISTS chgraph.meta (
        key String, value String, version UInt64
    ) ENGINE = ReplacingMergeTree(version) ORDER BY (key)""",
]


def create_all(sess) -> None:
    for stmt in DDL:
        sess.query(stmt)
