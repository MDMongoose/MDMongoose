"""Matching: cleaning, comparisons, fuzzy rules, constrained clustering, stable keys, incremental runs,
steward decisions, the upgrade from Match Keys, the service layer and mdm_log."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mdm import golden, load, runlog  # noqa: E402
from mdm.hierarchy import RuleError  # noqa: E402
from mdm.matching import cluster, keys, run as mr  # noqa: E402
from mdm.matching.spec import (METHOD_SPLINK, ROLE_FUZZY, ROLE_SPLINK, STATUS_NOT, STATUS_OPEN, load_spec,  # noqa: E402
                               parse_blocking, parse_condition)
from mdm.matching.standardise import check_steps, clean_values  # noqa: E402
from mdm.model import K, clone  # noqa: E402
from mdm.permissions import LEVEL_UIDS, READER, User  # noqa: E402
from mdm.repo import MemoryRepo  # noqa: E402
from mdm.seed import all_seed, upgrade_matching  # noqa: E402
from backend.service import Forbidden, MdmService  # noqa: E402
from mdm.uid import to_uid  # noqa: E402

from test_core import ACCOUNT, ADMIN, ALICE, CUSTOMER, KNA1, MATCH_RULE, load_demo, source_rows  # noqa: E402

SUPPLIER_RULE, EMPLOYEE_RULE = to_uid(2260), to_uid(2360)


@pytest.fixture
def svc():
    s = MdmService(MemoryRepo(all_seed("workspace", "mdm_staging", demo=True)))
    load_demo(s, match=False)
    return s


def recs(svc, table=KNA1):
    return {r["properties"].get("NAME1") or r["properties"].get("Name"): r for r in svc.repo.children(table)}


# ---------------------------------------------------------------- building blocks
def test_cleaning_steps():
    assert clean_values(["Trim", "Lower-case", "Remove accents", "Drop legal forms", "Remove punctuation"],  # legacy name
                        ["  ACME GmbH & Co. KG ", "Müller Logistik Kft.", "Sartor s.r.o.", ""]) == \
        ["acme", "muller logistik", "sartor", None]
    from mdm.matching.standardise import EMAIL_CLEANING, POSTCODE_CLEANING, phone_cleaning
    assert clean_values(phone_cleaning("49"), ["+49 89 5550100", "089 5550100", "0049 89 5550100"]) == ["49895550100"] * 3
    assert clean_values(EMAIL_CLEANING, [" JD@Example.com", "nope"]) == ["jd@example.com", None]
    assert clean_values(POSTCODE_CLEANING, ["d-80 331", "1051"]) == ["D80331", "1051"]
    # the removed steps still run in rules mdm_init has not upgraded yet
    assert clean_values(["Phone +49"], ["089 5550100"]) == ["49895550100"]
    assert clean_values(["Empty markers", "Upper-case", "Replace '[^A-Z0-9]' ''"], ["n/a", "de 123.456-789"]) == \
        [None, "DE123456789"]
    assert clean_values(["Country"], ["DE", "x"], {"de": "germany"}) == ["germany", "x"]
    assert check_steps(["Trim", "Bogus"]) == ["unknown cleaning step 'Bogus'"]
    assert check_steps(["Replace 'a'"])  # needs pattern and replacement


def test_conditions_and_blocking_parse():
    c = parse_condition("Legal Name >= 0.92 review 0.85")
    assert (c.field, c.link, c.review) == ("Legal Name", 0.92, 0.85)
    with pytest.raises(RuleError):
        parse_condition("Legal Name ~ 0.9")
    parts = parse_blocking("Country & Legal Name[:3] & Phone[-7:]")
    assert [(p.field, p.first, p.last) for p in parts] == [("Country", None, None), ("Legal Name", 3, None),
                                                          ("Phone", None, 7)]


def test_constrained_union_find_respects_not_the_same():
    # a–b exact, b–c linked, but a and c are "Not the same": c stays apart (also through the chain)
    groups, ev, blocked = cluster.cluster(["a", "b", "c"], [("a", "b", "VAT")], [], [("a", "c")],
                                          [("b", "c", 0.99, "Splink")])
    assert sorted(map(sorted, groups)) == [["a", "b"], ["c"]]
    assert ev["a"] == (1.0, "Exact key") and blocked == [("b", "c", "Splink")]


def test_stable_keys_majority_and_release():
    prev = {"r1": "MDM-A", "r2": "MDM-A", "r3": "MDM-B", "r4": None}
    out, released = keys.assign([{"r1", "r2", "r3"}, {"r4"}], prev, lambda n: [500_000 + i for i in range(n)])
    by = {frozenset(g): (k, new) for k, g, new in out}
    assert by[frozenset({"r1", "r2", "r3"})] == ("MDM-A", False)
    assert by[frozenset({"r4"})][1] is True and released == {"MDM-B"}


# ---------------------------------------------------------------- runs
def test_run_writes_golden_uid_and_survivorship_groups_by_it(svc):
    out = mr.run(svc.repo, MATCH_RULE)
    assert out["stats"]["records"] == 5 and out["stats"]["new_entities"] == 4
    k, a = recs(svc), recs(svc, ACCOUNT)
    acme, acme_sf = k["ACME GmbH"]["properties"], a["ACME Gmbh"]["properties"]
    assert acme["Golden UID"] == acme_sf["Golden UID"] and acme["Matched By"] == "Exact key"
    assert acme["Match Score"] == 1.0 and k["Acme Holding"]["properties"]["Matched By"] == "Alone"
    res = golden.refresh(svc.repo, svc.catalog, CUSTOMER)
    assert res["golden"] == 4 and res["unmatched"] == 0
    assert svc.repo.get(acme["Golden UID"])["parent_uid"] == CUSTOMER
    # nothing changed -> an incremental run touches nothing
    again = mr.run(svc.repo, MATCH_RULE)
    assert again["stats"]["changed"] == 0 and again["stats"]["records_written"] == 0


def test_preview_writes_nothing(svc):
    out = mr.run(svc.repo, MATCH_RULE, mode="full", preview=True)
    assert out["stats"]["new_entities"] == 4 and all(r["uid"].startswith("new #") for r in out["rows"])
    assert not any("Golden UID" in (r["properties"] or {}) for r in svc.repo.children(KNA1))


def test_reload_keeps_matching_properties(svc):
    mr.run(svc.repo, MATCH_RULE)
    before = recs(svc)["ACME GmbH"]["properties"]["Golden UID"]
    rows = source_rows("src_sap_kna1")
    rows[0]["TELF1"], rows[0]["_record_hash"] = "+49 1", rows[0]["_record_hash"] + "x"
    load.merge_records(svc.repo, svc.repo.get(KNA1), rows)
    assert recs(svc)["ACME GmbH"]["properties"]["Golden UID"] == before


def test_incremental_change_and_steward_decisions(svc):
    mr.run(svc.repo, MATCH_RULE)
    golden.refresh(svc.repo, svc.catalog, CUSTOMER)
    k, a = recs(svc), recs(svc, ACCOUNT)
    acme_key = k["ACME GmbH"]["properties"]["Golden UID"]
    # Same entity: Acme Holding joins ACME; the next run re-matches only those entities
    mr.decide(svc.repo, MATCH_RULE, k["Acme Holding"]["uid"], k["ACME GmbH"]["uid"], "same", "anna", "holding")
    out = mr.run(svc.repo, MATCH_RULE)
    assert out["stats"]["why"]["decisions"] == 2 and out["stats"]["affected"] == 3
    assert out["stats"]["merged"] == 1 and out["stats"]["retired"] == 1
    holding = svc.repo.get(k["Acme Holding"]["uid"])["properties"]
    assert holding["Golden UID"] == acme_key and holding["Matched By"] == "Steward"
    # Not this entity: the Salesforce record leaves, even though the VAT ID (an exact key) is equal
    mr.not_this_entity(svc.repo, MATCH_RULE, a["ACME Gmbh"]["uid"], "peter")
    out = mr.run(svc.repo, MATCH_RULE)
    assert out["stats"]["split"] == 1 and out["stats"]["blocked_by_decisions"] >= 1
    sf = svc.repo.get(a["ACME Gmbh"]["uid"])["properties"]
    assert sf["Golden UID"] != acme_key and sf["Matched By"] == "Alone"
    assert svc.repo.get(k["ACME GmbH"]["uid"])["properties"]["Golden UID"] == acme_key  # the UID stays
    # survivorship: one update, one new, one retired
    res = golden.refresh(svc.repo, svc.catalog, CUSTOMER)
    assert res["created"] == 1 and res["retired"] == 1
    # an edited record is re-matched alone (plus its entity)
    r = clone(svc.repo.get(k["Initech Zrt."]["uid"]) if "Initech Zrt." in k else svc.repo.get(a["Initech Zrt."]["uid"]))
    r["properties"]["Name" if r["parent_uid"] == ACCOUNT else "NAME1"] = "Initech Kft."
    svc.repo.upsert([r], "t")
    out = mr.run(svc.repo, MATCH_RULE)
    assert out["stats"]["why"]["new_or_changed"] == 1


def test_review_queue_and_details(svc):
    # a low review threshold on the name rule puts Acme Holding vs ACME GmbH in review
    spec = load_spec(svc.repo, MATCH_RULE)
    fr = [r.to_dict() for r in spec.fuzzy_rules]
    fr[0]["conditions"][0].update(link=0.95, review=0.6)
    svc.save_match_rule(ALICE, MATCH_RULE, {"fuzzy_rules": fr})
    mr.run(svc.repo, MATCH_RULE)
    q = mr.review_queue(svc.repo, MATCH_RULE)
    assert q["open_count"] >= 1
    p = next(x for x in q["open"] if {"Acme Holding"} & {x["a"]["name"].split(" · ")[-1], x["b"]["name"].split(" · ")[-1]})
    d = mr.details(svc.repo, MATCH_RULE, p["a"]["uid"], p["b"]["uid"])
    assert d["explanation"]["band"] == "review" and d["verdict"].startswith("review")
    name = next(f for f in d["fields"] if f["field"] == "Legal Name")
    assert name["a_clean"] in ("acme", "acme holding")
    # deciding the pair turns it into a permanent decision; the open pair is not re-created
    pair = svc.repo.get(p["uid"])
    svc.match_decide(ALICE, CUSTOMER, {"pair": pair["uid"], "status": "not", "comment": "different company"})
    assert svc.repo.get(p["uid"])["properties"]["Status"] == STATUS_NOT
    mr.run(svc.repo, MATCH_RULE, mode="full")
    assert svc.repo.get(p["uid"])["properties"]["Status"] == STATUS_NOT
    assert all(x["uid"] != p["uid"] for x in mr.review_queue(svc.repo, MATCH_RULE)["open"])


def test_exact_only_rule_and_one_rule_per_table(svc):
    ed = svc.match_rule_editor(ALICE, MATCH_RULE)
    assert ed["method"] == "fuzzy" and [f["name"] for f in ed["fields"]][:2] == ["Legal Name", "VAT ID"]
    with pytest.raises(RuleError, match="already a source"):
        svc.save_match_rule(ALICE, SUPPLIER_RULE, {"sources": [KNA1]})
    with pytest.raises(RuleError, match="no Match Field"):
        svc.save_match_rule(ALICE, MATCH_RULE, {"blocking": ["Nope[:3]"]})
    svc.save_match_rule(ALICE, MATCH_RULE, {"method": "exact"})
    out = mr.run(svc.repo, MATCH_RULE)
    assert out["stats"]["method"] == "exact" and out["stats"]["pairs_scored"] == 0


def test_permissions_on_decisions(svc):
    mr.run(svc.repo, MATCH_RULE)
    k = recs(svc)
    perms = svc.resolve_permissions(ADMIN, [{"group": "readers", "level": "Reader"},
                                            {"group": "modifiers", "level": "Modifier"}])
    root = svc.repo.get(K.ROOT)
    svc.repo.upsert([dict(root, steward=perms)], "test")
    bob, mia = User("bob@example.com", {"readers"}), User("mia@example.com", {"modifiers"})
    assert svc.match_review(bob, CUSTOMER)["open_count"] >= 0
    with pytest.raises(Forbidden):
        svc.match_decide(bob, k["ACME GmbH"]["uid"], {"a": k["ACME GmbH"]["uid"], "b": k["Acme Holding"]["uid"],
                                                     "status": "same"})
    # a Modifier decides from the record without any permission on Model › Rules
    out = svc.match_decide(mia, k["ACME GmbH"]["uid"], {"a": k["ACME GmbH"]["uid"], "b": k["Acme Holding"]["uid"],
                                                        "status": "same"})
    assert out["status"] == "Same entity" and LEVEL_UIDS[READER]


def test_upgrade_from_match_keys():
    """An older deployment: survivorship had Match Keys, golden records listed their _members."""
    seeds = [n for n in all_seed("workspace", "mdm_staging", demo=True)
             if K.NT_MATCH_RULE not in n["types"] and not ({K.NT_MATCH_FIELD, K.NT_FUZZY_RULE} & set(n["types"]))]
    repo = MemoryRepo(seeds)
    s = MdmService(repo)
    for m in (CUSTOMER, to_uid(2240), to_uid(2340)):
        n = clone(repo.get(m))
        n["properties"].pop("Match Rule", None)
        repo.upsert([n], "t")
    sr = clone(repo.get(to_uid(2040)))
    sr["properties"].update({"Match Keys": ["VAT ID"], "Match Threshold": 0.8})
    repo.upsert([sr], "t")
    load_demo(s, match=False)
    acme = recs(s)["ACME GmbH"]
    g = repo.allocate(1)[0]
    from mdm.model import new_node
    old_golden = new_node(g, "ACME GmbH", [to_uid(2101)], repo.get(CUSTOMER), origin="generated",
                          properties={"_members": [acme["uid"]]})
    repo.upsert([old_golden], "t")
    notes = upgrade_matching(repo)
    assert any("new Match Rule" in n for n in notes)
    rule = repo.get(repo.get(CUSTOMER)["properties"]["Match Rule"])
    spec = load_spec(repo, rule["uid"])
    assert spec.method == "exact" and [f.name for f in spec.exact_fields()] == ["VAT ID"]
    assert "Match Keys" not in repo.get(to_uid(2040))["properties"]
    assert repo.get(acme["uid"])["properties"]["Golden UID"] == old_golden["uid"]
    mr.run(repo, rule["uid"])  # the first run keeps the existing golden UID by majority
    assert repo.get(acme["uid"])["properties"]["Golden UID"] == old_golden["uid"]
    assert upgrade_matching(repo) == []  # idempotent


def test_runlog_memory_sink():
    sink = runlog.MemoryLogSink()
    with runlog.activity(sink, "match", "MDM-X", "incremental", "anna", "42") as lg:
        lg.stats["records"] = 3
    with pytest.raises(ValueError):
        with runlog.activity(sink, "golden", "MDM-Y", None, "anna", "42"):
            raise ValueError("boom")
    rows = sink.read(job_run_id="42")
    assert {r["status"] for r in rows} == {"SUCCESS", "FAILED"}
    assert next(r for r in rows if r["activity"] == "match")["stats"]["records"] == 3


def test_splink_train_and_score(svc):
    pytest.importorskip("splink")
    from mdm.seed import DEMO_SOURCE_ROWS, DEMO_TABLES
    import hashlib
    import json
    repo = svc.repo
    for table_uid, src in list(DEMO_TABLES.items())[:3]:
        spec = DEMO_SOURCE_ROWS[src]
        rows = []
        for r in spec["rows"][:400]:
            d = dict(zip(spec["columns"], r))
            d["_record_hash"] = hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()
            rows.append(d)
        load.merge_records(repo, repo.get(table_uid), rows)
    r = clone(repo.get(MATCH_RULE))
    r["properties"]["Method"] = METHOD_SPLINK
    ups = [r]
    for c in repo.children(MATCH_RULE):
        if K.NT_MATCH_FIELD in c["types"] and c["properties"]["Role"] == ROLE_FUZZY:
            c = clone(c)
            c["properties"]["Role"] = ROLE_SPLINK
            ups.append(c)
    repo.upsert(ups, "t")
    with pytest.raises(RuleError, match="no trained Splink model"):
        mr.run(repo, MATCH_RULE)
    res = mr.train(repo, MATCH_RULE, "anna")
    assert res["active"] and res["records"] > 500
    out = mr.run(repo, MATCH_RULE)
    assert out["stats"]["method"] == "splink" and out["stats"]["links"] > 50
    q = mr.review_queue(repo, MATCH_RULE)
    if q["open"]:
        p = q["open"][0]
        d = mr.details(repo, MATCH_RULE, p["a"]["uid"], p["b"]["uid"])
        assert d["explanation"]["method"] == "splink" and d["explanation"]["comparisons"]
    ed = svc.match_rule_editor(ALICE, MATCH_RULE)
    assert ed["model"]["comparisons"] and ed["model"]["prior_one_in"] > 1
    assert STATUS_OPEN


def test_inline_params_for_pipelines():
    """Lakeflow pipelines wrap spark.sql without args=: SparkExecutor inlines the values instead."""
    from mdm.sqlrepo import SparkExecutor, inline_params

    sql = "SELECT * FROM t WHERE kind = :k AND n = :n AND x = 'a:b' AND `c:d` = 1 AND y::string = :s -- :k"
    out = inline_params(sql, {"k": "NodeType", "n": 3, "s": "it's \\ ok"})
    assert out == ("SELECT * FROM t WHERE kind = 'NodeType' AND n = 3 AND x = 'a:b' AND `c:d` = 1 "
                   "AND y::string = 'it\\'s \\\\ ok' -- :k")

    class PatchedSpark:  # spark.sql(query) only, like the pipeline runtime
        seen = []

        def sql(self, q):
            self.seen.append(q)

            class DF:
                def collect(self_inner):
                    return []
            return DF()

    sp = PatchedSpark()
    assert SparkExecutor(sp).query("SELECT :v", {"v": None}) == [] and sp.seen == ["SELECT NULL"]


def test_editor_test_pair_uses_unsaved_rule(svc):
    mr.run(svc.repo, MATCH_RULE)
    ed = svc.match_rule_editor(ALICE, MATCH_RULE)
    assert ed["sample_pairs"], "the editor offers pairs to try the rule on"
    p = ed["sample_pairs"][0]
    saved = svc.match_test_pair(ALICE, MATCH_RULE, {"a": p["a"], "b": p["b"]})
    assert saved["explanation"]["method"] == "fuzzy"
    # loosen every condition (unsaved): the same pair now links
    fr = [dict(r, conditions=[dict(c, link=0.0, review=None) for c in r["conditions"]]) for r in ed["fuzzy_rules"]]
    loose = svc.match_test_pair(ALICE, MATCH_RULE, {"a": p["a"], "b": p["b"], "fuzzy_rules": fr})
    assert loose["explanation"]["band"] == "link"
    assert load_spec(svc.repo, MATCH_RULE).fuzzy_rules[0].conditions[0].link != 0.0  # nothing was saved


# ---------------------------------------------------------------- record filters, cleaned golden values
def test_record_filter_parse_and_eval():
    from mdm.matching.filters import compile_filter, parse_filters
    assert parse_filters(["MDM-1=LOEVM IS NULL", "MDM-2=status = 'A'", "bad"]) == {"MDM-1": "LOEVM IS NULL",
                                                                                  "MDM-2": "status = 'A'"}
    f = compile_filter("LOEVM IS NULL AND NOT is_test OR country = 'DE'")
    assert f({"LOEVM": None, "is_test": "false"}) and not f({"LOEVM": "X", "is_test": "0"})
    assert f({"LOEVM": "X", "country": "DE"}) and not f({"LOEVM": None, "is_test": "true"})
    assert compile_filter("name LIKE 'ACME%'")({"NAME": "ACME GmbH"})  # columns are case-insensitive
    with pytest.raises(RuleError):
        compile_filter("LOEVM ~ 1")


def test_record_filter_leaves_records_out_of_matching_and_golden(svc):
    mr.run(svc.repo, MATCH_RULE)
    holding = recs(svc)["Acme Holding"]
    assert holding["properties"].get("Golden UID")
    rule = clone(svc.repo.get(MATCH_RULE))
    rule["properties"]["Source Filters"] = [f"{KNA1}=NAME1 NOT LIKE 'Acme Holding%'"]
    svc.repo.upsert([rule], "t")
    pv = mr.run(svc.repo, MATCH_RULE, preview=True)  # the preview says which entity loses records
    assert any(r["action"] == "Retired" and "record filter" in r["why"] for r in pv["rows"])
    out = mr.run(svc.repo, MATCH_RULE)  # the fingerprint changed: everything is re-matched
    assert out["stats"]["filtered_out"] == 1 and out["stats"]["left_by_filter"] == 1
    assert not svc.repo.get(holding["uid"])["properties"].get("Golden UID")
    res = golden.refresh(svc.repo, svc.catalog, CUSTOMER)
    assert res["golden"] == 3 and res["unmatched"] == 0  # filtered records are not "not matched yet"
    st = svc.match_blocking(ADMIN, MATCH_RULE, {})
    assert {s["uid"]: (s["passed"], s["records"]) for s in st["sources"]}[KNA1] == (2, 3)
    with pytest.raises(RuleError):
        svc.save_match_rule(ADMIN, MATCH_RULE, {"filters": {KNA1: "NAME1 ~ 1"}})


def test_use_cleaned_value_applies_the_match_field_cleaning(svc):
    mr.run(svc.repo, MATCH_RULE)
    vat = next(c for c in svc.repo.children(to_uid(2040)) if c["name"] == "VAT ID")
    n = clone(vat)
    n["properties"]["Use Cleaned Value"] = True
    svc.repo.upsert([n], "t")
    golden.refresh(svc.repo, svc.catalog, CUSTOMER)
    vats = {g["properties"].get("VAT ID") for g in svc.repo.children(CUSTOMER)}
    assert vats and all(v is None or v == v.upper().replace(" ", "") for v in vats)
    pv = golden.preview(svc.repo, svc.catalog, to_uid(2040), "Legal Name",
                        {"rules": [{"property": "Legal Name", "cleaned": True}]})
    assert pv["value"] == pv["value"].lower() and "cleaned" in pv["reason"]


# ---------------------------------------------------------------- Standardization Tables
def test_standardize_step_uses_the_standardization_table(svc):
    from mdm.matching.spec import STD_KEEP_SOURCE, standardization_table
    countries = to_uid(2030)
    t = standardization_table(svc.repo, countries)
    assert t["key"] == "Name" and t["default"] == "Other" and t["map"]["deu"] == "Germany"
    step = [f"Standardize {countries}"]
    assert clean_values(step, ["DE", "deu", " Germany ", "Atlantis", None], tables={countries: t}) == \
        ["Germany", "Germany", "Germany", "Other", None]
    keep = dict(t, default=None)
    assert clean_values(step, ["AT", "Atlantis"], tables={countries: keep}) == ["Austria", "Atlantis"]
    assert check_steps(["Standardize"]) and not check_steps(step)
    spec = load_spec(svc.repo, MATCH_RULE)
    assert countries in spec.tables and not spec.validate()
    # Keep source on the node itself
    n = clone(svc.repo.get(countries))
    n["properties"]["Default Method"] = STD_KEEP_SOURCE
    svc.repo.upsert([n], "t")
    assert standardization_table(svc.repo, countries)["default"] is None
    # a node without the NodeType is refused
    assert load_spec(svc.repo, MATCH_RULE, {"fields": [dict(f.to_dict(), cleaning=[f"Standardize {KNA1}"])
                                                       for f in spec.fields]}).validate()


def test_own_property_values_are_checked(svc):
    from backend.service import FieldError
    countries = to_uid(2030)
    row = next(r for r in svc.node_view(ADMIN, countries)["properties"] if r["name"] == "Standardization Columns")
    assert row["choices"][:1] == ["Name"] and "Alpha3" in row["choices"]
    svc.edit(ADMIN, countries, {"Standardization Columns": "Alpha2, Name"})
    assert svc.repo.get(countries)["properties"]["Standardization Columns"] == ["Alpha2", "Name"]
    with pytest.raises(FieldError):
        svc.edit(ADMIN, countries, {"Standardization Key": "Bogus"})


def test_drop_words_and_regex_replace(svc):
    from mdm.matching.spec import standardization_tables
    from mdm.seed import upgrade_cleaning
    legal = to_uid(1450)
    steps = ["Trim", "Lower-case", "Remove accents", f"Drop words {legal}", "Remove punctuation"]
    tables, errs = standardization_tables(svc.repo, steps)
    assert not errs and "Kft." in tables[legal]["words"]
    assert clean_values(steps, ["  ACME GmbH & Co. KG ", "Müller Logistik Kft.", "Sartor s. r. o.", "Banco S.A."],
                        tables=tables) == ["acme", "muller logistik", "sartor", "banco"]
    assert clean_values(["Regex Replace '[^A-Z0-9]' ''"], ["DE 123-456"]) == ["DE123456"]
    assert check_steps(["Drop words", "Regex Replace 'a'"]) and not check_steps(steps)
    assert standardization_tables(svc.repo, [f"Drop words {KNA1}"])[1]  # not an Enum
    # old rules: renamed by mdm_init, same result
    f = next(n for n in svc.repo.children(MATCH_RULE) if n["name"] == "VAT ID")
    svc.repo.upsert([dict(f, properties={**f["properties"], "Cleaning": ["Upper-case", "Replace ' ' ''"]})], "t")
    assert upgrade_cleaning(svc.repo) and svc.repo.get(f["uid"])["properties"]["Cleaning"][-1] == "Regex Replace ' ' ''"
    assert not upgrade_cleaning(svc.repo)


def test_upgrade_rewrites_removed_steps(svc):
    from mdm.seed import upgrade_cleaning
    f = next(n for n in svc.repo.children(MATCH_RULE) if n["name"] == "Phone")
    old = ["Empty markers", "Phone +36", "Email", "Postcode", "Country"]
    svc.repo.upsert([dict(f, properties={**f["properties"], "Cleaning": old})], "t")
    assert upgrade_cleaning(svc.repo)
    new = svc.repo.get(f["uid"])["properties"]["Cleaning"]
    assert new[:4] == ["Empty markers", "Digits only", "Regex Replace '^00' ''", "Regex Replace '^0' '36'"]
    assert new[-1] == f"Standardize {to_uid(2030)}" and not check_steps(new) and "Email" not in new


def test_comparisons_soundex_damerau_and_renames():
    import duckdb

    from mdm.matching import compare
    from mdm.matching.spec import COMPARISON_BY_NAME, COMPARISON_NAMES
    from mdm.seed import rename_comparisons
    sim = lambda c, a, b: duckdb.sql(f"SELECT {compare.similarity(c, repr(a), repr(b))}").fetchone()[0]  # noqa: E731
    assert sim("Soundex", "Robert", "Rupert") == 1.0 and sim("Soundex", "Robert", "Rubin") == 0.0
    assert duckdb.sql(f"SELECT {compare._soundex(repr('Tymczak'))}, {compare._soundex(repr('Pfister'))}").fetchone() == \
        ("T522", "P236")
    assert sim("Damerau-Levenshtein", "abcd", "acbd") == 0.75 and sim("Levenshtein", "abcd", "acbd") == 0.5
    assert sim("Jaro-Winkler order ignored", "Doe John", "John Doe") == 1.0
    assert all(compare.describe(c) for c in COMPARISON_NAMES)
    assert COMPARISON_BY_NAME["Jaro-Winkler"] == to_uid(1422) and COMPARISON_BY_NAME["Soundex"] == to_uid(1432)
    repo = MemoryRepo(all_seed("workspace", "mdm_staging", demo=True))
    old = repo.get(to_uid(1422))
    repo.upsert([dict(old, name="Name")], "t")
    assert rename_comparisons(repo) and repo.get(to_uid(1422))["name"] == "Jaro-Winkler"
    # Email, Phone, Postcode, Date, Number are gone: a field that used one gets Exact or Levenshtein
    assert "Email" not in COMPARISON_NAMES and repo.get(to_uid(1426)) is None
    f = next(n for n in repo.children(MATCH_RULE) if n["name"] == "Phone")
    repo.upsert([dict(f, properties={**f["properties"], "Comparison": to_uid(1427)})], "t")
    rename_comparisons(repo)
    assert repo.get(f["uid"])["properties"]["Comparison"] == to_uid(1424)
