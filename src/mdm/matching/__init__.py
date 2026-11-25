"""Matching: decides which source records are one entity and writes the Golden UID on each record.

Runs before survivorship (mdm.golden), which only groups records by Golden UID.

    spec          Match Rule node (+ Match Field / Fuzzy Rule children, Pairs folder) -> MatchSpec
    standardise   cleaning steps -> DuckDB SQL (one library for exact keys, fuzzy rules, Splink, previews)
    compare       comparison types: similarity (fuzzy rules) and comparison levels (Splink)
    candidates    DuckDB tables of cleaned records; exact-key edges; blocking pairs (incremental scope)
    fuzzy_rules   "all conditions of any rule" -> score, band, explanation
    splink_model  train with Splink (DuckDB); score / explain from the saved model in plain DuckDB SQL
    cluster       steward decisions + constrained union-find
    keys          stable Golden UID per cluster (majority of the records' previous UIDs)
    writer        record updates (Golden UID, Match Score, Matched By, Matched At, _match_hash), Match Pairs
    run           one run: incremental / full, preview / apply; match details; steward decisions
"""
