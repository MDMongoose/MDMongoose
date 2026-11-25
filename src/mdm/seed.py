"""Seed nodes: System and Model definitions (always), demo data (optional).

Every seeded node has a fixed uid number (<= 100 000), so seeding is idempotent:
the init job MERGEs these rows and never duplicates them.
"""

from __future__ import annotations

from . import names
from .matching.standardise import EMAIL_CLEANING, POSTCODE_CLEANING, phone_cleaning
from .model import K, new_node
from .permissions import LEVEL_UIDS, MANAGER, MODIFIER, READER, SUPERVISOR
from .uid import to_uid

# The Required Level of each seeded action (only users at or above it can run it)
ACTION_LEVELS = {
    "add_child": MANAGER, "edit": MODIFIER, "delete": MANAGER, "lock": SUPERVISOR, "change_parent": MANAGER,
    "add_child_link": MANAGER, "edit_children": MODIFIER, "delete_all_children": MANAGER, "export": READER,
    "configure_load": MODIFIER, "load_children": MANAGER, "refresh_golden": MANAGER, "edit_rule": MANAGER,
    "run_export": MODIFIER, "edit_match_rule": MANAGER, "train_matching": MANAGER, "run_matching": MANAGER,
    "review_matches": MODIFIER, "record_matching": MODIFIER, "golden_matching": MODIFIER,
}


def _pd(name, ptype, group="General", priority=0):
    return {"name": name, "ptype": ptype, "group": group, "priority": priority}


def _action(num, name, parent, handler, params, group, ui, mode, level=None):
    lvl = level if level is not None else ACTION_LEVELS.get(ui, MODIFIER)
    return new_node(num, name, [K.NT_FOLDER, K.NT_ACTION], parent, icon="bolt", locked=True, max_child_depth=0,
                    properties={"handler": handler, "params": params, "action_group": group,
                                "ui": ui, "mode": mode, "Required Level": LEVEL_UIDS[lvl]})


def _enum(num, name, parent, values, **kw):
    """An enum list: a node with the NodeType Enum; its children are the values (UID, Name)."""
    folder = new_node(num, name, [K.NT_FOLDER, K.NT_ENUM], parent, icon="list", max_child_depth=1,
                      properties={"kind": "Enum"}, **kw)
    return [folder] + [new_node(num + 1 + i, v, [K.NT_ORDERED], folder, icon="tag", max_child_depth=0,
                                sort_order=float(i + 1)) for i, v in enumerate(values) if v]


def _ntype(num, name, parent, icon, defs, actions, locked=True):
    return new_node(num, name, [K.NT_FOLDER], parent, icon=icon, locked=locked, max_child_depth=0,
                    properties={"kind": "NodeType", "default_icon": icon, "property_defs": defs,
                                "actions": actions})


def system_nodes() -> list[dict]:
    n: list[dict] = []
    root = new_node(1, "MDM", [K.NT_FOLDER], None, icon="globe", locked=True,
                    owner="admins", steward=[to_uid(60)])
    system = new_node(2, "System", [K.NT_FOLDER], root, icon="lock", locked=True)
    ptypes = new_node(3, "PropertyTypes", [K.NT_FOLDER], system, icon="tag", locked=True, max_child_depth=1)
    actions = new_node(4, "Actions", [K.NT_FOLDER], system, icon="bolt", locked=True, max_child_depth=1)
    ntypes = new_node(5, "NodeTypes", [K.NT_FOLDER], system, icon="cube", locked=True, max_child_depth=1)
    perms = new_node(6, "Permissions", [K.NT_FOLDER], system, icon="shield", locked=True, max_child_depth=1,
                     allowed_child_types=[K.NT_PERMISSION])
    counter = new_node(7, "UID Counter", [K.NT_FOLDER], system, icon="hash", locked=True, max_child_depth=0,
                       properties={"next": 100_001})
    n += [root, system, ptypes, actions, ntypes, perms, counter]

    pt_info = {
        "UID": ("STRING", "read-only, generated: MDM- + base36"),
        "Number": ("BIGINT", "number input"),
        "Float": ("DOUBLE", "decimal input"),
        "String": ("STRING", "text input"),
        "Timestamp": ("TIMESTAMP", "date-time input, many formats on paste"),
        "Link": ("STRING (uid)", "picker: one exact node, parameter = target NodeType"),
        "Enum": ("STRING (uid)", "dropdown of the children of the enum root"),
        "Array": ("ARRAY<STRING>", "ordered list of nodes of a NodeType, or Array(String)"),
        "Expression": ("computed", "SQL expression over the node's properties; read-only value"),
        "Icon": ("STRING", "icon picker"),
        "Boolean": ("BOOLEAN", "checkbox; empty = FALSE"),
        "OwnProperty": ("STRING", "a property name of the node's rows (its children's NodeTypes) or Name"),
    }
    for i, (name, (storage, editor)) in enumerate(pt_info.items()):
        n.append(new_node(10 + i, name, [K.NT_FOLDER], ptypes, icon="tag", locked=True, max_child_depth=0,
                          properties={"kind": "PropertyType", "storage": storage, "editor": editor}))

    n += [
        _action(30, "Add Child", actions, "mdm.actions.add_child", "parentUID, Properties[]", "Children", "add_child", "sync"),
        _action(31, "Edit", actions, "mdm.actions.edit", "UID, Changes", "Self", "edit", "sync"),
        _action(32, "Delete", actions, "mdm.actions.delete", "UID", "Self", "delete", "sync"),
        _action(33, "Lock/Unlock", actions, "mdm.actions.toggle_lock", "UID", "Self", "lock", "sync"),
        _action(34, "Change Parent", actions, "mdm.actions.change_parent", "UID, newParentUID", "Self", "change_parent", "sync"),
        _action(35, "Add Child Link", actions, "mdm.actions.add_child_link", "parentUID, linkUID, LinkType", "Children", "add_child_link", "sync"),
        _action(36, "Edit Children", actions, "mdm.actions.edit_children", "UID, Changes[]", "Children", "edit_children", "sync"),
        _action(37, "Delete All Child", actions, "mdm.actions.delete_all_children", "UID", "Children", "delete_all_children", "sync"),
        _action(38, "Export", actions, "mdm.export.run", "UID, Format, ChildDepth, Balanced", "Self", "export", "sync"),
    ]

    # General (first) holds only UID and Name; the structural node fields come last, before Governance
    node_defs = [
        _pd("UID", "UID"), _pd("Name", "String"), _pd("Types", "Array(NodeType)", "Node", 98),
        _pd("Parent", "Link(Node)", "Node", 98), _pd("Origin", "String", "Node", 98),
        _pd("Last Modified", "Timestamp", "Node", 98), _pd("Icon", "Icon", "Node", 98),
        _pd("Max Child Depth", "Number", "Node", 98), _pd("Allowed Child Type", "Array(NodeType)", "Node", 98),
        _pd("Owner", "String", "Governance", 99), _pd("Steward", "Array(Permission)", "Governance", 99),
        _pd("Locked", "Boolean", "Governance", 99),
    ]
    n += [
        _ntype(50, "Node", ntypes, "box", node_defs,
               [K.A_CHANGE_PARENT, K.A_EDIT, K.A_LOCK, K.A_DELETE, K.A_EXPORT,
                K.A_ADD_CHILD, K.A_EDIT_CHILDREN, K.A_DELETE_ALL_CHILDREN, K.A_ADD_CHILD_LINK]),
        _ntype(51, "Folder", ntypes, "folder", [], []),
        _ntype(52, "Ordered", ntypes, "hash", [_pd("Order", "Float", "Node", 98)], []),
        _ntype(53, "Table", ntypes, "table", [
            _pd("Table Name", "String", "Table", 10), _pd("Keys", "Array(String)", "Table", 10),
            _pd("Name Expression", "Expression(SQL)", "Table", 10), _pd("Record Type", "Link(NodeType)", "Table", 10),
            _pd("Column Mapping", "Array(String)", "Table", 10), _pd("Expectations", "Array(String)", "Table", 10),
            _pd("Load Mode", "Enum(LoadModes)", "Load", 12), _pd("Schedule", "String", "Load", 12),
            _pd("Delete Mode", "Enum(DeleteModes)", "Load", 12), _pd("Record Icon", "Icon", "Load", 12),
            _pd("Run Golden After Load", "Boolean", "Load", 12)],
            [K.A_CONFIGURE_LOAD, K.A_LOAD_CHILDREN]),
        _ntype(54, "Record", ntypes, "file", [
            _pd("Source Key", "String", "Record", 30), _pd("Loaded At", "Timestamp", "Record", 30),
            _pd("Record Hash", "String", "Record", 30),
            # written by matching (read-only for users): the entity the record belongs to
            _pd("Golden UID", "Link(Node)", "Matching", 40), _pd("Match Score", "Float", "Matching", 40),
            _pd("Matched By", "String", "Matching", 40), _pd("Matched At", "Timestamp", "Matching", 40)],
            [K.A_RECORD_MATCHING]),
        _ntype(55, "Permission", ntypes, "shield", [
            _pd("Group", "String", "Permission", 10), _pd("Level", "Enum(PermissionLevels)", "Permission", 10)], []),
        _ntype(56, "Enum", ntypes, "list", [], []),
        _ntype(57, "Action", ntypes, "bolt", [_pd("Required Level", "Enum(PermissionLevels)", "Action", 10)], []),
        # a node that shows another node (the origin) in a second place: it has its own Parent, Owner, Steward,
        # Max Child Depth, Allowed Child Type and children; every other value is the origin's (see mdm/links.py)
        _ntype(58, "Child Link", ntypes, "link", [_pd("Linked Node", "Link(Node)", "Link", 1),
                                                  _pd("Link Type", "Enum(LinkTypes)", "Link", 1)], []),
    ]
    # Default permission: the workspace group "users" may manage everything below the root
    # (edit, lock, add children, move, delete — not the Governance group).
    n.append(new_node(60, "users · Manager", [K.NT_PERMISSION], perms, locked=True, max_child_depth=0,
                      properties={"Group": "users", "Level": LEVEL_UIDS[MANAGER]}))
    return n


def model_nodes() -> list[dict]:
    n: list[dict] = []
    by = {x["uid"]: x for x in system_nodes()}
    root = by[K.ROOT]
    model = new_node(1000, "Model", [K.NT_FOLDER], root, icon="cube")
    tables = new_node(1001, "Tables", [K.NT_FOLDER], model, icon="table")
    ntypes = new_node(1002, "NodeTypes", [K.NT_FOLDER], model, icon="cube")
    actions = new_node(1003, "Actions", [K.NT_FOLDER], model, icon="bolt")
    enums = new_node(1004, "Enums", [K.NT_FOLDER], model, icon="list")
    rules = new_node(1005, "Rules", [K.NT_FOLDER], model, icon="sliders",
                     allowed_child_types=[K.NT_SURVIVORSHIP_RULE, K.NT_MATCH_RULE])
    exports = new_node(1006, "Exports", [K.NT_FOLDER], model, icon="download",
                       allowed_child_types=[K.NT_EXPORT])
    n += [model, tables, ntypes, actions, enums, rules, exports]

    n += [
        _ntype(1100, "Master Data", ntypes, "star",
               [_pd("Match Rule", "Link(Match Rule)", "Master Data", 10),
                _pd("Survivorship Rule", "Link(Survivorship Rule)", "Master Data", 10),
                _pd("Processing", "Enum(ProcessingModes)", "Master Data", 10)],   # empty = One Machine
               [K.A_EDIT_RULE, K.A_RUN_MATCHING, K.A_REVIEW_MATCHES, K.A_REFRESH_GOLDEN], locked=False),
        _ntype(1101, "Survivorship Rule", ntypes, "sliders", [
            _pd("Golden Record Type", "Link(NodeType)", "Rule", 10), _pd("Sources", "Array(Table)", "Rule", 10)],
            [K.A_EDIT_RULE, K.A_REFRESH_GOLDEN], locked=False),
        _ntype(1102, "Property Rule", ntypes, "sliders", [
            _pd("Property", "String", "Survivorship", 10), _pd("Strategy", "Enum(Strategies)", "Survivorship", 10),
            _pd("Source Priority", "Array(Table)", "Survivorship", 10),
            _pd("Source Mapping", "String", "Survivorship", 10),
            _pd("Recency Column", "String", "Survivorship", 10), _pd("Custom Function", "String", "Survivorship", 10),
            _pd("Ignore Empty", "Boolean", "Eligibility", 20), _pd("UserDefined Wins", "Boolean", "Eligibility", 20),
            _pd("Eligible If", "Expression(SQL)", "Eligibility", 20),
            _pd("Use Cleaned Value", "Boolean", "Survivorship", 10)], [], locked=False),
        _action(1200, "Configure Load", actions, "mdm.actions.edit", "UID, Changes", "Load", "configure_load", "sync"),
        _action(1201, "Load Childs", actions, "pipeline:mdm_load", "UID", "Children", "load_children", "pipeline"),
        _action(1202, "Refresh Golden Records", actions, "mdm.golden.refresh", "UID", "Survivorship", "refresh_golden", "job"),
        _action(1203, "Edit Rule", actions, "mdm.actions.edit_children", "UID, PropertyRules[]", "Survivorship", "edit_rule", "sync"),
        _ntype(1103, "Export", ntypes, "download", [
            _pd("Export Type", "Enum(ExportTypes)", "Export", 10), _pd("Object Name", "String", "Export", 10),
            _pd("Root Node", "Link(Node)", "Export", 10), _pd("Child Depth", "Number", "Export", 10),
            _pd("Balanced", "Enum(BalanceModes)", "Export", 10), _pd("Include Properties", "Boolean", "Export", 10),
            _pd("Include UIDs", "Boolean", "Export", 10), _pd("Balanced Flag", "Boolean", "Export", 10),
            _pd("CSV Delimiter", "String", "File", 12), _pd("Excel Sheets", "Enum(ExcelSheets)", "File", 12),
            _pd("Excel Header", "Enum(ExcelHeaders)", "File", 12),
            _pd("Location", "String", "Status", 14), _pd("Last Run", "String", "Status", 14)],
            [K.A_RUN_EXPORT], locked=False),
        _action(1204, "Run Export", actions, "job:mdm_export", "UID", "Export", "run_export", "job"),
        # matching: a step before survivorship (see mdm.matching)
        _ntype(1104, "Match Rule", ntypes, "merge", [
            _pd("Sources", "Array(Table)", "Match Rule", 10), _pd("Method", "Enum(MatchMethods)", "Match Rule", 10),
            _pd("Blocking Rules", "Array(String)", "Match Rule", 10), _pd("Source Filters", "Array(String)", "Match Rule", 10),
            _pd("Link Threshold", "Float", "Thresholds", 12), _pd("Review Threshold", "Float", "Thresholds", 12),
            _pd("Model", "String", "Status", 14), _pd("Last Run", "String", "Status", 14)],
            [K.A_EDIT_MATCH_RULE, K.A_TRAIN_MATCHING, K.A_RUN_MATCHING, K.A_REVIEW_MATCHES], locked=False),
        _ntype(1105, "Match Field", ntypes, "hash", [
            _pd("Role", "Enum(MatchRoles)", "Match Field", 10), _pd("Comparison", "Enum(Comparisons)", "Match Field", 10),
            _pd("Cleaning", "Array(String)", "Match Field", 10), _pd("Source Mapping", "String", "Match Field", 10)],
            [], locked=False),
        _ntype(1106, "Fuzzy Rule", ntypes, "sliders", [_pd("Conditions", "Array(String)", "Fuzzy Rule", 10)],
               [], locked=False),
        _ntype(1107, "Match Pair", ntypes, "pair", [
            _pd("Record A", "Link(Node)", "Pair", 10), _pd("Record B", "Link(Node)", "Pair", 10),
            _pd("Score", "Float", "Pair", 10), _pd("Reason", "String", "Pair", 10),
            _pd("Status", "Enum(PairStatus)", "Decision", 12), _pd("Comment", "String", "Decision", 12),
            _pd("Decided By", "String", "Decision", 12), _pd("Decided At", "Timestamp", "Decision", 12),
            _pd("Scored At", "Timestamp", "Decision", 12)], [], locked=False),
        # a node whose children are a lookup: a value found in any Standardization Column becomes the key
        _ntype(1108, "Standardization Table", ntypes, "list", [
            _pd("Standardization Columns", "Array(OwnProperty)", "Standardization", 10),
            _pd("Standardization Key", "OwnProperty", "Standardization", 10),
            _pd("Default Method", "Enum(StandardizationDefaults)", "Standardization", 10),
            _pd("Default Value", "String", "Standardization", 10)], [], locked=False),
        _action(1205, "Edit Match Rule", actions, "mdm.matching.edit", "UID, Fields[], Rules[]", "Matching",
                "edit_match_rule", "sync"),
        _action(1206, "Train Match Model", actions, "job:mdm_match_train", "UID", "Matching", "train_matching", "job"),
        _action(1207, "Run Matching", actions, "job:mdm_match", "UID, Mode, Preview", "Matching", "run_matching", "job"),
        _action(1208, "Review Matches", actions, "mdm.matching.review", "UID", "Matching", "review_matches", "sync"),
        _action(1209, "Matching", actions, "mdm.matching.record", "UID", "Matching", "record_matching", "sync"),
        _action(1210, "Merge / Split", actions, "mdm.matching.golden", "UID", "Matching", "golden_matching", "sync"),
    ]

    enum_defs = [
        (1300, "LoadModes", ["full", "incremental"]),
        (1310, "LoadTypes", ["SCD1 (overwrite)", "Append"]),
        (1320, "Strategies", ["Source Priority", "Most Recent", "Custom"]),
        (1330, "LinkTypes", ["same_as", "related_to", "source"]),
        (1340, "PermissionLevels", ["Reader", "Modifier", "Supervisor", "Manager", "Governor", "Admin"]),
        (1350, "ExportFormats", ["csv", "xlsx"]),
        (1360, "BalanceModes", ["none", "balanced"]),
        (1370, "DeleteModes", ["Soft delete", "Hard delete", "Keep"]),
        (1380, "ExportTypes", ["Materialized view", "View", "Excel", "CSV"]),
        (1390, "ExcelSheets", ["One sheet", "Sheet per level"]),
        (1395, "ExcelHeaders", ["Styled", "Plain"]),
        (1410, "MatchMethods", ["Exact keys only", "Exact keys + fuzzy rules", "Exact keys + Splink"]),
        (1415, "MatchRoles", ["Exact key", "Fuzzy rule", "Splink", "Blocking only"]),
        (1420, "Comparisons", ["Exact", "Jaro-Winkler", "Jaro-Winkler order ignored", "Levenshtein", "Jaccard",
                               None, None, None, None, None, "Damerau-Levenshtein", "Soundex"]),  # None: removed values
        (1435, "PairStatus", ["Open", "Same entity", "Not the same"]),
        (1445, "StandardizationDefaults", ["Keep source", "Override default"]),
        (1450, "LegalForms", LEGAL_FORMS),
        (1490, "ProcessingModes", ["One Machine", "Distributed"]),   # mdm.processing
    ]
    for num, name, values in enum_defs:
        n += _enum(num, name, enums, values)

    n.append(new_node(1400, "Reference Data", [K.NT_FOLDER], root, icon="book"))
    n.append(new_node(1401, "Master Data", [K.NT_FOLDER], root, icon="star"))
    return n


def demo_nodes(source_catalog: str, source_schema: str) -> list[dict]:
    """Countries, two source tables (SAP kna1, Salesforce account), a golden record type,
    a survivorship rule and the Master Data node Customer."""
    n: list[dict] = []
    by = {x["uid"]: x for x in system_nodes() + model_nodes()}
    m_tables, m_types, m_rules = by[K.MODEL_TABLES], by[K.MODEL_NODE_TYPES], by[K.MODEL_RULES]
    ref, master = by[K.REFERENCE_DATA], by[K.MASTER_DATA]

    sap_model = new_node(2010, "SAP ECC", [K.NT_FOLDER], m_tables, icon="server")
    sf_model = new_node(2011, "Salesforce", [K.NT_FOLDER], m_tables, icon="server")
    country_t = _ntype(2100, "Country", m_types, "flag", [
        _pd("Alpha2", "String", "Country", 10), _pd("Alpha3", "String", "Country", 10),
        _pd("Currency", "String", "Country", 10)], [], locked=False)
    golden_t = _ntype(2101, "Customer Golden Record", m_types, "star", [
        _pd("Legal Name", "String", "Golden Record", 10), _pd("VAT ID", "String", "Golden Record", 10),
        _pd("Country", "Enum(Countries)", "Golden Record", 10), _pd("City", "String", "Golden Record", 10),
        _pd("Email", "String", "Golden Record", 10), _pd("Phone", "String", "Golden Record", 10),
        _pd("Survivorship Rule", "Link(Survivorship Rule)", "Survivorship", 20),
        _pd("Match Score", "Float", "Survivorship", 20)], [K.A_GOLDEN_MATCHING], locked=False)
    kna1_t = _ntype(2102, "kna1Record", sap_model, "file", [
        _pd(c, "String", "kna1", 10) for c in ["KUNNR", "NAME1", "LAND1", "ORT01", "STCEG", "TELF1", "SMTP_ADDR"]],
        [], locked=False)
    acc_t = _ntype(2103, "accountRecord", sf_model, "file", [
        _pd(c, "String", "account", 10)
        for c in ["Id", "Name", "BillingCity", "BillingCountry", "VAT_Number__c", "Email__c", "Phone"]],
        [], locked=False)
    n += [sap_model, sf_model, country_t, golden_t, kna1_t, acc_t]

    countries = new_node(2030, "Countries", [K.NT_FOLDER, K.NT_ENUM, K.NT_STANDARDIZATION_TABLE], ref, icon="flag",
                         max_child_depth=1, allowed_child_types=[country_t["uid"]],
                         properties={"kind": "Enum", "Standardization Columns": ["Alpha2", "Alpha3", "Name"],
                                     "Standardization Key": "Name", "Default Method": to_uid(1447),
                                     "Default Value": "Other"})
    n.append(countries)
    for i, (name, a2, a3, cur) in enumerate([("Hungary", "HU", "HUN", "HUF"), ("Germany", "DE", "DEU", "EUR"),
                                             ("Switzerland", "CH", "CHE", "CHF"), ("Austria", "AT", "AUT", "EUR"),
                                             ("Czechia", "CZ", "CZE", "CZK"), ("Slovakia", "SK", "SVK", "EUR"),
                                             ("Romania", "RO", "ROU", "RON")]):
        n.append(new_node(2031 + i, name, [country_t["uid"], K.NT_ORDERED], countries, max_child_depth=0,
                          sort_order=float(i + 1), properties={"Alpha2": a2, "Alpha3": a3, "Currency": cur}))

    sap_ref = new_node(2020, "SAP ECC", [K.NT_FOLDER], ref, icon="server", allowed_child_types=[K.NT_TABLE])
    kna1 = new_node(2021, "kna1", [K.NT_TABLE], sap_ref, max_child_depth=1,
                    allowed_child_types=[kna1_t["uid"]],
                    properties={"Table Name": f"{source_catalog}.{source_schema}.src_sap_kna1",
                                "Keys": ["KUNNR"], "Load Mode": to_uid(1301), "Record Type": kna1_t["uid"],
                                "Name Expression": "KUNNR || ' · ' || NAME1", "Delete Mode": to_uid(1371),
                                "Schedule": "0 2 * * *", "Run Golden After Load": True,
                                "Expectations": ["KUNNR IS NOT NULL"]})
    sf_ref = new_node(2022, "Salesforce", [K.NT_FOLDER], ref, icon="server", allowed_child_types=[K.NT_TABLE])
    account = new_node(2023, "account", [K.NT_TABLE], sf_ref, max_child_depth=1,
                       allowed_child_types=[acc_t["uid"]],
                       properties={"Table Name": f"{source_catalog}.{source_schema}.src_sfdc_account",
                                   "Keys": ["Id"], "Load Mode": to_uid(1301), "Record Type": acc_t["uid"],
                                   "Name Expression": "Name", "Delete Mode": to_uid(1371),
                                   "Schedule": "0 2 * * *", "Run Golden After Load": True,
                                   "Expectations": ["Id IS NOT NULL"]})
    n += [sap_ref, kna1, sf_ref, account]
    shop_model = new_node(2012, "Webshop", [K.NT_FOLDER], m_tables, icon="server")
    shop_t = _rtype(2104, "shopCustomerRecord", shop_model, DEMO_COLUMNS["src_shop_customers"], "webshop")
    shop_ref = new_node(2024, "Webshop", [K.NT_FOLDER], ref, icon="server", allowed_child_types=[K.NT_TABLE])
    shop = _table(2025, "customers", shop_ref, f"{source_catalog}.{source_schema}.src_shop_customers",
                  ["customer_id"], shop_t, "company", ["customer_id IS NOT NULL", "company IS NOT NULL"])
    n += [shop_model, shop_t, shop_ref, shop]

    rule = new_node(2040, "Customer survivorship v2", [K.NT_SURVIVORSHIP_RULE], m_rules, max_child_depth=1,
                    allowed_child_types=[K.NT_PROPERTY_RULE],
                    properties={"Golden Record Type": golden_t["uid"],
                                "Sources": [kna1["uid"], account["uid"], shop["uid"]]})
    n.append(rule)
    k, a, w = kna1["uid"], account["uid"], shop["uid"]
    prop_rules = [
        ("Legal Name", 1321, [k, a, w], f"{k}=NAME1; {a}=Name; {w}=company", {}),
        ("VAT ID", 1321, [k, a, w], f"{k}=STCEG; {a}=VAT_Number__c; {w}=vat_id",
         {"Eligible If": "value RLIKE '^[A-Z]{2,3}[0-9]+$'"}),
        ("Country", 1321, [k, a, w], f"{k}=LAND1; {a}=BillingCountry; {w}=country", {}),
        ("City", 1322, [a, k, w], f"{k}=ORT01; {a}=BillingCity; {w}=city", {"Recency Column": "Loaded At"}),
        ("Email", 1323, [a, w, k], f"{k}=SMTP_ADDR; {a}=Email__c; {w}=email",
         {"Custom Function": "mdm_custom.survivorship.valid_email_prefer_personal", "Eligible If": "value LIKE '%@%'"}),
        ("Phone", 1322, [k, a, w], f"{k}=TELF1; {a}=Phone; {w}=phone", {"Recency Column": "Loaded At"}),
    ]
    for i, (prop, strategy, order, mapping, extra) in enumerate(prop_rules):
        props = {"Property": prop, "Strategy": to_uid(strategy), "Source Priority": order,
                 "Source Mapping": mapping, "Ignore Empty": True, "UserDefined Wins": True}
        props.update(extra)
        n.append(new_node(2041 + i, prop, [K.NT_PROPERTY_RULE, K.NT_ORDERED], rule, max_child_depth=0,
                          sort_order=float(i + 1), properties=props))

    mrule = _match_rule(2060, "Customer match", m_rules, [k, a, w], to_uid(1412),
                        ["Country & Legal Name[:3]", "Phone[-7:]", "Email"], [
        ("Legal Name", "fuzzy", "Jaro-Winkler", NAME_CLEANING, {k: "NAME1", a: "Name", w: "company"}),
        ("VAT ID", "exact", "Exact", VAT_CLEANING, {k: "STCEG", a: "VAT_Number__c", w: "vat_id"}),
        ("Email", "fuzzy", "Exact", EMAIL_CLEANING, {k: "SMTP_ADDR", a: "Email__c", w: "email"}),
        ("Phone", "fuzzy", "Levenshtein", PHONE_CLEANING, {k: "TELF1", a: "Phone", w: "phone"}),
        ("City", "fuzzy", "Levenshtein", ["Trim", "Lower-case", "Remove accents"], {k: "ORT01", a: "BillingCity", w: "city"}),
        ("Country", "blocking", "Exact", COUNTRY_CLEANING, {k: "LAND1", a: "BillingCountry", w: "country"}),
    ], [
        ("Same name and country", ["Legal Name >= 0.95 review 0.85", "Country >= 1"]),
        ("Same phone, similar name", ["Phone >= 0.9", "Legal Name >= 0.8 review 0.7"]),
        ("Same email, similar name", ["Email >= 1", "Legal Name >= 0.7"]),
    ])
    n += mrule
    n.append(new_node(2050, "Customer", [K.NT_FOLDER, K.NT_MASTER_DATA], master, icon="building",
                      max_child_depth=1, allowed_child_types=[golden_t["uid"]],
                      properties={"Match Rule": mrule[0]["uid"], "Survivorship Rule": rule["uid"]}))
    n += supplier_nodes(source_catalog, source_schema, by, sap_model, sap_ref)
    n += employee_nodes(source_catalog, source_schema, by)
    n += product_nodes(by)
    n += export_nodes(source_catalog, by)
    return n


def export_nodes(catalog: str, by: dict) -> list[dict]:
    """Demo Export nodes: a materialized view, a CSV file and a view."""
    folder = by[K.MODEL_EXPORTS]
    t = [K.NT_EXPORT]
    common = {"Include Properties": True, "Include UIDs": True}
    return [
        new_node(2500, "Customer golden records", t, folder, icon="download", max_child_depth=0, properties={
            **common, "Export Type": to_uid(1381), "Object Name": "customer_golden_records", "Root Node": to_uid(2050),
            "Child Depth": 1, "Balanced": to_uid(1361), "Balanced Flag": False,
            "Location": f"{catalog}.{names.EXPORT}.customer_golden_records"}),
        new_node(2501, "Product hierarchy (CSV)", t, folder, icon="download", max_child_depth=0, properties={
            **common, "Export Type": to_uid(1384), "Object Name": "product_hierarchy", "Root Node": to_uid(3000),
            "Child Depth": 6, "Balanced": to_uid(1362), "Balanced Flag": True, "CSV Delimiter": ";",
            "Location": f"{names.volume_path(catalog)}/product_hierarchy.csv"}),
        new_node(2502, "Countries (view)", t, folder, icon="download", max_child_depth=0, properties={
            **common, "Export Type": to_uid(1382), "Object Name": "countries", "Root Node": to_uid(2030),
            "Child Depth": 1, "Balanced": to_uid(1361), "Balanced Flag": False,
            "Location": f"{catalog}.{names.EXPORT}.countries",
            "Last Run": "not created yet — click Run Export (views are created by the app)"}),
    ]


# ------------------------------------------------------------------ demo helpers
def _rtype(num, name, parent, columns, group):
    """Record NodeType of a source table: one String property per source column."""
    return _ntype(num, name, parent, "file", [_pd(c, "String", group, 10) for c in columns], [], locked=False)


def _table(num, name, parent, full_name, keys, record_t, name_expr, expectations):
    return new_node(num, name, [K.NT_TABLE], parent, max_child_depth=1, allowed_child_types=[record_t["uid"]],
                    properties={"Table Name": full_name, "Keys": keys, "Load Mode": to_uid(1301),
                                "Record Type": record_t["uid"], "Name Expression": name_expr,
                                "Delete Mode": to_uid(1371), "Schedule": "0 2 * * *", "Run Golden After Load": True,
                                "Expectations": expectations})


LEGAL_FORMS = ["GmbH", "AG", "Zrt.", "Kft.", "Nyrt.", "Bt.", "Ltd", "Limited", "Inc", "Incorporated", "Corp",
               "Corporation", "Co", "LLC", "SA", "SARL", "SRL", "sro", "BV", "NV", "PLC", "SE", "KG", "OHG", "UG", "SpA",
               "SAS", "KK", "Pty", "s.r.o.", "a.s.", "s.r.l.", "S.A.", "B.V.", "N.V.", "S.p.A."]  # Enum LegalForms (1450)
NAME_CLEANING = ["Trim", "Lower-case", "Remove accents", f"Drop words {to_uid(1450)}", "Remove punctuation"]
VAT_CLEANING = ["Empty markers", "Upper-case", "Regex Replace '[^A-Z0-9]' ''"]
PHONE_CLEANING = ["Empty markers"] + phone_cleaning("49")
COUNTRY_CLEANING = [f"Standardize {to_uid(2030)}"]  # the Countries Standardization Table: DE / DEU / Germany -> Germany


def _match_rule(num, name, parent, sources, method, blocking, fields, fuzzy_rules, link=0.95, review=0.70):
    """A Match Rule with its Match Fields (num+1..) and Fuzzy Rules (num+20..).
    fields: (name, role, comparison, cleaning, {source uid: record property})."""
    from .matching.spec import COMPARISON_BY_NAME, ROLE_BY_KEY
    rule = new_node(num, name, [K.NT_MATCH_RULE], parent, icon="merge", max_child_depth=2,
                    allowed_child_types=[K.NT_MATCH_FIELD, K.NT_FUZZY_RULE, K.NT_FOLDER],
                    properties={"Sources": sources, "Method": method, "Blocking Rules": blocking,
                                "Link Threshold": link, "Review Threshold": review})
    out = [rule]
    for i, (fname, role, comp, cleaning, mapping) in enumerate(fields):
        out.append(new_node(num + 1 + i, fname, [K.NT_MATCH_FIELD, K.NT_ORDERED], rule, icon="hash", max_child_depth=0,
                            sort_order=float(i + 1), properties={
                                "Role": ROLE_BY_KEY[role], "Comparison": COMPARISON_BY_NAME[comp], "Cleaning": cleaning,
                                "Source Mapping": "; ".join(f"{u}={c}" for u, c in mapping.items())}))
    for i, (rname, conds) in enumerate(fuzzy_rules):
        out.append(new_node(num + 20 + i, rname, [K.NT_FUZZY_RULE, K.NT_ORDERED], rule, icon="sliders",
                            max_child_depth=0, sort_order=float(100 + i), properties={"Conditions": conds}))
    return out


def _rule(num, name, parent, golden_t, sources, prop_rules):
    """prop_rules: (property, strategy uid num, [source uids by priority], {source uid: column}, extra)."""
    rule = new_node(num, name, [K.NT_SURVIVORSHIP_RULE], parent, max_child_depth=1,
                    allowed_child_types=[K.NT_PROPERTY_RULE],
                    properties={"Golden Record Type": golden_t["uid"], "Sources": sources})
    out = [rule]
    for i, (prop, strategy, order, mapping, extra) in enumerate(prop_rules):
        props = {"Property": prop, "Strategy": to_uid(strategy), "Source Priority": order,
                 "Source Mapping": "; ".join(f"{u}={c}" for u, c in mapping.items()),
                 "Ignore Empty": True, "UserDefined Wins": True, **extra}
        out.append(new_node(num + 1 + i, prop, [K.NT_PROPERTY_RULE, K.NT_ORDERED], rule, max_child_depth=0,
                            sort_order=float(i + 1), properties=props))
    return out


def supplier_nodes(cat: str, sch: str, by: dict, sap_model: dict, sap_ref: dict) -> list[dict]:
    """Suppliers: SAP lfa1 + Coupa → Supplier golden records."""
    m_tables, m_types, m_rules = by[K.MODEL_TABLES], by[K.MODEL_NODE_TYPES], by[K.MODEL_RULES]
    ref, master = by[K.REFERENCE_DATA], by[K.MASTER_DATA]
    lfa1_t = _rtype(2201, "lfa1Record", sap_model, DEMO_COLUMNS["src_sap_lfa1"], "lfa1")
    coupa_model = new_node(2202, "Coupa", [K.NT_FOLDER], m_tables, icon="server")
    coupa_t = _rtype(2203, "coupaSupplierRecord", coupa_model, DEMO_COLUMNS["src_coupa_suppliers"], "coupa")
    golden_t = _ntype(2204, "Supplier Golden Record", m_types, "truck", [
        _pd("Legal Name", "String", "Golden Record", 10), _pd("VAT ID", "String", "Golden Record", 10),
        _pd("Country", "Enum(Countries)", "Golden Record", 10), _pd("City", "String", "Golden Record", 10),
        _pd("Email", "String", "Golden Record", 10), _pd("Phone", "String", "Golden Record", 10),
        _pd("IBAN", "String", "Payment", 12), _pd("Payment Terms", "String", "Payment", 12),
        _pd("Commodity", "String", "Purchasing", 14),
        _pd("Survivorship Rule", "Link(Survivorship Rule)", "Survivorship", 20),
        _pd("Match Score", "Float", "Survivorship", 20)], [K.A_GOLDEN_MATCHING], locked=False)
    lfa1 = _table(2210, "lfa1", sap_ref, f"{cat}.{sch}.src_sap_lfa1", ["LIFNR"], lfa1_t,
                  "LIFNR || ' · ' || NAME1", ["LIFNR IS NOT NULL"])
    coupa_ref = new_node(2211, "Coupa", [K.NT_FOLDER], ref, icon="server", allowed_child_types=[K.NT_TABLE])
    coupa = _table(2212, "suppliers", coupa_ref, f"{cat}.{sch}.src_coupa_suppliers", ["id"], coupa_t,
                   "name", ["id IS NOT NULL"])
    s, c = lfa1["uid"], coupa["uid"]
    rule = _rule(2220, "Supplier survivorship", m_rules, golden_t, [s, c], [
        ("Legal Name", 1321, [c, s], {s: "NAME1", c: "name"}, {}),  # Coupa keeps proper casing
        ("VAT ID", 1321, [s, c], {s: "STCEG", c: "tax_id"}, {"Eligible If": "value RLIKE '^[A-Z]{2,3}[0-9]+$'"}),
        ("Country", 1321, [s, c], {s: "LAND1", c: "country"}, {}),
        ("City", 1322, [c, s], {s: "ORT01", c: "city"}, {"Recency Column": "Loaded At"}),
        ("Email", 1321, [c], {c: "primary_email"}, {"Eligible If": "value LIKE '%@%'"}),
        ("Phone", 1321, [s, c], {s: "TELF1", c: "phone"}, {}),
        ("IBAN", 1321, [s], {s: "IBAN"}, {"Eligible If": "value RLIKE '^[A-Z]{2}[0-9]{2}[A-Z0-9]+$'"}),
        ("Payment Terms", 1321, [c, s], {s: "ZTERM", c: "payment_terms"}, {}),
        ("Commodity", 1321, [c], {c: "commodity"}, {}),
    ])
    mrule = _match_rule(2260, "Supplier match", m_rules, [s, c], to_uid(1412), ["Country & Legal Name[:3]", "Phone[-7:]"], [
        ("Legal Name", "fuzzy", "Jaro-Winkler", NAME_CLEANING, {s: "NAME1", c: "name"}),
        ("VAT ID", "exact", "Exact", VAT_CLEANING, {s: "STCEG", c: "tax_id"}),
        ("Phone", "fuzzy", "Levenshtein", PHONE_CLEANING, {s: "TELF1", c: "phone"}),
        ("Country", "blocking", "Exact", COUNTRY_CLEANING, {s: "LAND1", c: "country"}),
    ], [
        ("Same name and country", ["Legal Name >= 0.95 review 0.88", "Country >= 1"]),
        ("Same phone, similar name", ["Phone >= 0.9", "Legal Name >= 0.8 review 0.7"]),
    ])
    supplier = new_node(2240, "Supplier", [K.NT_FOLDER, K.NT_MASTER_DATA], master, icon="truck", max_child_depth=1,
                        allowed_child_types=[golden_t["uid"]],
                        properties={"Match Rule": mrule[0]["uid"], "Survivorship Rule": rule[0]["uid"]})
    return [lfa1_t, coupa_model, coupa_t, golden_t, lfa1, coupa_ref, coupa, *rule, *mrule, supplier]


def employee_nodes(cat: str, sch: str, by: dict) -> list[dict]:
    """Employees: Workday workers + Active Directory users → Employee golden records (matched by Employee ID)."""
    m_tables, m_types, m_rules = by[K.MODEL_TABLES], by[K.MODEL_NODE_TYPES], by[K.MODEL_RULES]
    ref, master = by[K.REFERENCE_DATA], by[K.MASTER_DATA]
    wd_model = new_node(2301, "Workday", [K.NT_FOLDER], m_tables, icon="server")
    wd_t = _rtype(2302, "workerRecord", wd_model, DEMO_COLUMNS["src_workday_workers"], "workday")
    ad_model = new_node(2303, "Active Directory", [K.NT_FOLDER], m_tables, icon="server")
    ad_t = _rtype(2304, "adUserRecord", ad_model, DEMO_COLUMNS["src_ad_users"], "ad")
    golden_t = _ntype(2305, "Employee Golden Record", m_types, "user", [
        _pd("Employee ID", "String", "Employee", 10), _pd("Full Name", "String", "Employee", 10),
        _pd("Email", "String", "Employee", 10), _pd("Phone", "String", "Employee", 10),
        _pd("Job Title", "String", "Position", 12), _pd("Department", "String", "Position", 12),
        _pd("Manager ID", "String", "Position", 12), _pd("Country", "Enum(Countries)", "Position", 12),
        _pd("Office", "String", "Position", 12), _pd("Hire Date", "Timestamp", "Employment", 14),
        _pd("Status", "String", "Employment", 14),
        _pd("Survivorship Rule", "Link(Survivorship Rule)", "Survivorship", 20),
        _pd("Match Score", "Float", "Survivorship", 20)], [K.A_GOLDEN_MATCHING], locked=False)
    wd_ref = new_node(2310, "Workday", [K.NT_FOLDER], ref, icon="server", allowed_child_types=[K.NT_TABLE])
    wd = _table(2311, "workers", wd_ref, f"{cat}.{sch}.src_workday_workers", ["Employee_ID"], wd_t,
                "Full_Name", ["Employee_ID IS NOT NULL"])
    ad_ref = new_node(2312, "Active Directory", [K.NT_FOLDER], ref, icon="server", allowed_child_types=[K.NT_TABLE])
    ad = _table(2313, "users", ad_ref, f"{cat}.{sch}.src_ad_users", ["sAMAccountName"], ad_t,
                "displayName", ["sAMAccountName IS NOT NULL", "employeeID IS NOT NULL"])
    w, a = wd["uid"], ad["uid"]
    rule = _rule(2320, "Employee survivorship", m_rules, golden_t, [w, a], [
        ("Employee ID", 1321, [w, a], {w: "Employee_ID", a: "employeeID"}, {}),
        ("Full Name", 1321, [w, a], {w: "Full_Name", a: "displayName"}, {}),
        ("Email", 1321, [a], {a: "mail"}, {"Eligible If": "value LIKE '%@%'"}),
        ("Phone", 1321, [a], {a: "telephoneNumber"}, {}),
        ("Job Title", 1321, [w, a], {w: "Business_Title", a: "title"}, {}),
        ("Department", 1321, [w, a], {w: "Department", a: "department"}, {}),
        ("Manager ID", 1321, [w], {w: "Manager_ID"}, {}),
        ("Country", 1321, [w], {w: "Country"}, {}),
        ("Office", 1321, [a, w], {w: "Location", a: "physicalDeliveryOfficeName"}, {}),
        ("Hire Date", 1321, [w], {w: "Hire_Date"}, {}),
        ("Status", 1321, [w], {w: "Worker_Status"}, {}),
    ])
    mrule = _match_rule(2360, "Employee match", m_rules, [w, a], to_uid(1411), [], [
        ("Employee ID", "exact", "Exact", ["Trim", "Upper-case", "Empty markers"], {w: "Employee_ID", a: "employeeID"}),
    ], [])
    employee = new_node(2340, "Employee", [K.NT_FOLDER, K.NT_MASTER_DATA], master, icon="users", max_child_depth=1,
                        allowed_child_types=[golden_t["uid"]],
                        properties={"Match Rule": mrule[0]["uid"], "Survivorship Rule": rule[0]["uid"]})
    return [wd_model, wd_t, ad_model, ad_t, golden_t, wd_ref, wd, ad_ref, ad, *rule, *mrule, employee]


# Product hierarchy: Division › Category › Subcategory › Product Line › Product Family › Product
PRODUCT_TREE = {
    ("Tools", "wrench"): {
        "Power Tools": {
            "Drilling": {
                "Cordless Drills": {
                    ("ProDrill 18V", "TV-PD18", 129.0, 1.6): ["Body only", "2.0 Ah kit", "4.0 Ah kit",
                                                              "5.0 Ah kit + case", "Brushless 4.0 Ah kit"],
                    ("CompactDrill 12V", "TV-CD12", 89.0, 1.0): ["Body only", "2.0 Ah kit", "2× 2.0 Ah kit",
                                                                 "Right-angle kit", "Starter set"]},
                "Hammer Drills": {
                    ("RotoHammer SDS", "TV-RH", 179.0, 2.9): ["SDS-plus 800 W", "SDS-plus 1000 W", "SDS-plus 18V body",
                                                             "SDS-max 1300 W", "SDS-max 1500 W"]}},
            "Sawing": {
                "Circular Saws": {
                    ("CircuSaw 190", "TV-CS", 149.0, 3.8): ["165 mm 1200 W", "190 mm 1400 W", "190 mm 18V body",
                                                           "235 mm 2000 W", "Plunge 165 mm"]}}},
        "Hand Tools": {
            "Fastening": {
                "Screwdrivers": {
                    ("GripLine Screwdrivers", "TV-GL", 24.9, 0.6): ["Slotted set 6 pcs", "Phillips set 6 pcs",
                                                                   "Torx set 8 pcs", "VDE set 7 pcs",
                                                                   "Precision set 12 pcs"]}}}},
    ("Garden", "leaf"): {
        "Lawn Care": {
            "Mowing": {
                "Cordless Mowers": {
                    ("GreenCut 36V", "GD-GC36", 299.0, 16.0): ["34 cm", "38 cm", "42 cm", "46 cm self-propelled",
                                                               "46 cm kit"]},
                "Robotic Mowers": {
                    ("AutoMow", "GD-AM", 699.0, 9.5): ["400 m²", "600 m²", "1000 m²", "1500 m² GPS", "3000 m² GPS"]}},
            "Trimming": {
                "Hedge Trimmers": {
                    ("HedgeMaster", "GD-HM", 99.0, 3.2): ["45 cm 500 W", "55 cm 600 W", "60 cm 36V body",
                                                          "Pole 18V", "Shears 18V"]}}},
        "Watering": {
            "Hoses & Reels": {
                "Garden Hoses": {
                    ("FlexHose", "GD-FH", 19.9, 2.0): ['1/2" 15 m', '1/2" 25 m', '5/8" 25 m', '3/4" 50 m',
                                                       "Hose reel set 30 m"]}},
            "Irrigation": {
                "Drip Systems": {
                    ("DripKit", "GD-DK", 39.0, 1.2): ["Balcony starter", "Raised bed", "Hedge 25 m", "Greenhouse",
                                                      "Smart controller"]}}}},
}
PRODUCT_LEVELS = ["Division", "Category", "Subcategory", "Product Line", "Product Family"]


def _ean(n: int) -> str:
    body = f"599{n:09d}"
    check = (10 - sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body)) % 10) % 10
    return body + str(check)


def product_nodes(by: dict) -> list[dict]:
    """50 products, 6 levels deep under Master Data / Product, entered by hand (not loaded)."""
    m_types, enums, master = by[K.MODEL_NODE_TYPES], by[to_uid(1004)], by[K.MASTER_DATA]
    status = new_node(2410, "ProductStatus", [K.NT_FOLDER, K.NT_ENUM], enums, icon="list", max_child_depth=1,
                      properties={"kind": "Enum"})
    statuses = [new_node(2411 + i, v, [K.NT_ORDERED], status, icon="tag", max_child_depth=0, sort_order=float(i + 1))
                for i, v in enumerate(["Active", "Phase-out", "Discontinued"])]
    group_t = _ntype(2400, "Product Group", m_types, "folder", [
        _pd("Code", "String", "Product Group", 10), _pd("Level", "String", "Product Group", 10)], [], locked=False)
    product_t = _ntype(2401, "Product", m_types, "box", [
        _pd("SKU", "String", "Product", 10), _pd("EAN", "String", "Product", 10), _pd("Brand", "String", "Product", 10),
        _pd("Status", "Enum(ProductStatus)", "Product", 10), _pd("Launch Date", "Timestamp", "Product", 10),
        _pd("List Price", "Float", "Commercial", 12), _pd("Currency", "String", "Commercial", 12),
        _pd("Unit", "String", "Logistics", 14), _pd("Weight kg", "Float", "Logistics", 14)], [], locked=False)
    root = new_node(3000, "Product", [K.NT_FOLDER], master, icon="box", max_child_depth=6,
                    allowed_child_types=[group_t["uid"]])
    out = [status, *statuses, group_t, product_t, root]
    num = iter(range(3001, 3200))
    seq = iter(range(1, 1000))

    def walk(tree, parent, depth, brand, path_codes):
        for i, (key, sub) in enumerate(tree.items()):
            if depth < 4:
                name, icon = key if isinstance(key, tuple) else (key, None)
                code = "".join(w[0] for w in name.replace("&", "").split()).upper()[:3]
                node = new_node(next(num), name, [group_t["uid"], K.NT_ORDERED], parent, icon=icon or "folder",
                                sort_order=float(i + 1), properties={"Code": "-".join(path_codes + [code]),
                                                                     "Level": PRODUCT_LEVELS[depth]})
                out.append(node)
                walk(sub, node, depth + 1, brand or name, path_codes + [code])
            else:  # product family → 5 products
                fam, sku, price, weight = key
                fnode = new_node(next(num), fam, [group_t["uid"], K.NT_ORDERED], parent, icon="tag",
                                 sort_order=float(i + 1), allowed_child_types=[product_t["uid"]],
                                 properties={"Code": sku, "Level": "Product Family"})
                out.append(fnode)
                for j, variant in enumerate(sub):
                    n_ = next(seq)
                    st = statuses[2] if n_ in (7, 31) else statuses[1] if n_ % 9 == 0 else statuses[0]
                    out.append(new_node(next(num), f"{fam} {variant}", [product_t["uid"], K.NT_ORDERED], fnode,
                                        icon="box", max_child_depth=0, sort_order=float(j + 1), properties={
                                            "SKU": f"{sku}-{j + 1:03d}", "EAN": _ean(400000 + n_),
                                            "Brand": "Torvik" if brand == "Tools" else "Gardelia",
                                            "Status": st["uid"],
                                            "Launch Date": f"{2019 + (n_ * 7) % 8}-{(n_ * 5) % 12 + 1:02d}-01T00:00:00Z",
                                            "List Price": round(price * (1 + 0.35 * j), 2), "Currency": "EUR",
                                            "Unit": "pcs", "Weight kg": round(weight * (1 + 0.15 * j), 2)}))

    walk(PRODUCT_TREE, root, 0, None, [])
    return out


CURATED_ROWS = {  # hand-written edge cases, always first (tests use only these)
    "src_sap_kna1": {
        "columns": ["KUNNR", "NAME1", "LAND1", "ORT01", "STCEG", "TELF1", "SMTP_ADDR"],
        "rows": [
            ["0000100234", "ACME GmbH", "Germany", "München", "DE123456789", "+49 89 5550100", ""],
            ["0000100871", "Acme Holding", "Germany", "München", "", "", ""],
            ["0000100912", "Globex AG", "Switzerland", "Zug", "CHE123456789", "", ""],
        ],
    },
    "src_sfdc_account": {
        "columns": ["Id", "Name", "BillingCity", "BillingCountry", "VAT_Number__c", "Email__c", "Phone"],
        "rows": [
            ["0018X00002AbC", "ACME Gmbh", "Munich", "Germany", "DE123456789", "info@acme.example", "+49 89 5550199"],
            ["0018X00004XyZ", "Initech Zrt.", "Budapest", "Hungary", "", "", ""],
        ],
    },
}

DEMO_COLUMNS = {
    "src_sap_kna1": ["KUNNR", "NAME1", "LAND1", "ORT01", "STCEG", "TELF1", "SMTP_ADDR"],
    "src_sfdc_account": ["Id", "Name", "BillingCity", "BillingCountry", "VAT_Number__c", "Email__c", "Phone"],
    "src_shop_customers": ["customer_id", "company", "contact_name", "email", "phone", "city", "country", "vat_id",
                           "registered_at"],
    "src_sap_lfa1": ["LIFNR", "NAME1", "LAND1", "ORT01", "STCEG", "TELF1", "IBAN", "ZTERM"],
    "src_coupa_suppliers": ["id", "name", "country", "city", "tax_id", "primary_email", "phone", "payment_terms",
                            "commodity"],
    "src_workday_workers": ["Employee_ID", "Full_Name", "First_Name", "Last_Name", "Business_Title", "Department",
                            "Cost_Center", "Country", "Location", "Hire_Date", "Manager_ID", "Worker_Status"],
    "src_ad_users": ["sAMAccountName", "employeeID", "displayName", "mail", "telephoneNumber", "department", "title",
                     "physicalDeliveryOfficeName"],
}
# Table node uid → demo source table (local mode loads these in memory)
DEMO_TABLES = {to_uid(2021): "src_sap_kna1", to_uid(2023): "src_sfdc_account", to_uid(2025): "src_shop_customers",
               to_uid(2210): "src_sap_lfa1", to_uid(2212): "src_coupa_suppliers",
               to_uid(2311): "src_workday_workers", to_uid(2313): "src_ad_users"}


def demo_source_rows() -> dict:
    """{table: {columns, rows, curated}} — curated rows plus the generated demo data (mdm.demo_data)."""
    global _DEMO
    if _DEMO is None:
        from .demo_data import source_tables
        gen = source_tables()
        out = {}
        for name, cols in DEMO_COLUMNS.items():
            cur = CURATED_ROWS.get(name, {"rows": []})["rows"]
            g = gen[name]
            idx = [g["columns"].index(c) if c in g["columns"] else None for c in cols]
            rows = [list(r) for r in cur] + [[r[i] if i is not None else "" for i in idx] for r in g["rows"]]
            out[name] = {"columns": cols, "rows": rows, "curated": len(cur)}
        _DEMO = out
    return _DEMO


_DEMO = None


def __getattr__(name):  # `from mdm.seed import DEMO_SOURCE_ROWS` builds the demo data on first use
    if name == "DEMO_SOURCE_ROWS":
        return demo_source_rows()
    raise AttributeError(name)


def all_seed(source_catalog: str, source_schema: str, demo: bool) -> list[dict]:
    nodes = system_nodes() + model_nodes()
    if demo:
        nodes += demo_nodes(source_catalog, source_schema)
    uids = [x["uid"] for x in nodes]
    assert len(uids) == len(set(uids)), "duplicate seeded uid"
    return nodes


def upgrade_existing(repo, seeds: list[dict], user: str = "mdm_init") -> int:
    """After a new release: merge new property definitions / actions into seeded NodeTypes
    and new keys into seeded Action nodes that already exist, without touching user edits."""
    current = repo.get_many([s["uid"] for s in seeds])
    changed = []
    for s in seeds:
        cur = current.get(s["uid"])
        if not cur:
            continue
        sp, cp = s["properties"] or {}, dict(cur["properties"] or {})
        dirty = False
        if sp.get("kind") == "NodeType" and cp.get("kind") == "NodeType":
            names = {d["name"] for d in cp.get("property_defs", [])}
            add = [d for d in sp.get("property_defs", []) if d["name"] not in names]
            if add:
                cp["property_defs"] = list(cp.get("property_defs", [])) + add
                dirty = True
            if s.get("locked") and cur.get("locked"):  # system NodeTypes: group / priority follow the release
                want = {d["name"]: d for d in sp.get("property_defs", [])}
                defs = []
                for d in cp.get("property_defs", []):
                    w = want.get(d["name"])
                    if w and (d.get("group"), d.get("priority")) != (w["group"], w["priority"]):
                        d = {**d, "group": w["group"], "priority": w["priority"]}
                        dirty = True
                    defs.append(d)
                cp["property_defs"] = defs
            acts = [a for a in sp.get("actions", []) if a not in cp.get("actions", [])]
            if acts:
                cp["actions"] = list(cp.get("actions", [])) + acts
                dirty = True
        elif sp.get("Sources") and cp.get("Sources") is not None:  # seeded rule: new demo sources
            add = [u for u in sp["Sources"] if u not in cp["Sources"]]
            if add:
                cp["Sources"] = list(cp["Sources"]) + add
                dirty = True
        elif sp.get("Source Mapping") and cp.get("Source Mapping") is not None:  # its property rules
            have = {part.split("=", 1)[0].strip() for part in str(cp["Source Mapping"]).split(";") if "=" in part}
            add = [part.strip() for part in sp["Source Mapping"].split(";")
                   if "=" in part and part.split("=", 1)[0].strip() not in have]
            if add:
                cp["Source Mapping"] = "; ".join([str(cp["Source Mapping"]).strip("; ")] + add)
                cp["Source Priority"] = list(cp.get("Source Priority") or []) + [
                    u for u in sp.get("Source Priority") or [] if u not in (cp.get("Source Priority") or [])]
                dirty = True
        elif sp.get("handler"):
            for k, v in sp.items():
                if k not in cp:
                    cp[k] = v
                    dirty = True
        if dirty:
            n = dict(cur)
            n["properties"] = cp
            changed.append(n)
    if changed:
        repo.upsert(changed, user)
    return len(changed)


def upgrade_release(repo, seeds: list[dict], user: str = "mdm_init") -> list[str]:
    """One-off data upgrades of this release (idempotent). Returns notes.

    - Enum lists carry the NodeType Enum; actions the NodeType Action with a Required Level.
    - Permission levels: Read/Modify become Reader/Modifier; the default "users · Modify" grant on the
      root becomes "users · Manager", so users keep adding, moving and deleting nodes as before.
    """
    notes, changed = [], {}

    def touch(n):
        changed[n["uid"]] = changed.get(n["uid"]) or dict(n, properties=dict(n["properties"] or {}),
                                                          types=list(n["types"]))
        return changed[n["uid"]]

    for n in repo.by_kind("Enum"):
        if K.NT_ENUM not in n["types"]:
            touch(n)["types"].append(K.NT_ENUM)
    for n in repo.by_kind("Action"):
        p = n["properties"] or {}
        if K.NT_ACTION not in n["types"]:
            touch(n)["types"].append(K.NT_ACTION)
        if not p.get("Required Level"):
            touch(n)["properties"]["Required Level"] = LEVEL_UIDS[ACTION_LEVELS.get(p.get("ui"), MODIFIER)]
    for uid, old, new in ((LEVEL_UIDS[READER], "Read", "Reader"), (LEVEL_UIDS[MODIFIER], "Modify", "Modifier")):
        v = repo.get(uid)
        if v and v["name"] == old:
            touch(v)["name"] = new
    level_name = {LEVEL_UIDS[READER]: "Reader", LEVEL_UIDS[MODIFIER]: "Modifier"}
    for pn in repo.children(K.SYS_PERMISSIONS, limit=10_000):
        p = pn["properties"] or {}
        if pn["uid"] == to_uid(60) and pn["name"] == "users · Modify" and p.get("Level") == LEVEL_UIDS[MODIFIER]:
            t = touch(pn)
            t["name"], t["properties"]["Level"] = "users · Manager", LEVEL_UIDS[MANAGER]
            notes.append("default grant 'users · Modify' on the root is now 'users · Manager'")
            continue
        for old in ("Read", "Modify"):
            if pn["name"].endswith(f" · {old}") and level_name.get(p.get("Level")):
                touch(pn)["name"] = pn["name"][: -len(old)] + level_name[p["Level"]]
    if changed:
        repo.upsert(list(changed.values()), user)
        notes.append(f"upgraded {len(changed)} node(s) to enum/action types and the new permission levels")
    notes += upgrade_matching(repo, user)
    notes += upgrade_cleaning(repo, user)
    notes += rename_comparisons(repo, user)
    notes += upgrade_child_links(repo, user)
    return notes


def upgrade_child_links(repo, user: str = "mdm_init") -> list[str]:
    """Child links of this release (idempotent): the old child_links arrays become Child Link nodes."""
    from .links import convert_child_links
    link_types = {n["name"]: n["uid"] for n in repo.children(to_uid(1330))}
    n = convert_child_links(repo, user, link_types)
    return [f"{n} child link(s) converted to Child Link nodes"] if n else []


def rename_comparisons(repo, user: str = "mdm_init") -> list[str]:
    """Comparisons of this release (idempotent): Name -> Jaro-Winkler, Text -> Levenshtein, ... (same UIDs, new names);
    Email, Phone, Postcode, Date and Number removed — Match Fields using them get Exact or Levenshtein."""
    from .matching.spec import COMPARISONS, REMOVED_COMPARISONS
    notes, writes = [], []
    for n in repo.children(to_uid(1420)):
        if n["uid"] in COMPARISONS and n["name"] != COMPARISONS[n["uid"]]:
            writes.append(dict(n, name=COMPARISONS[n["uid"]]))
        elif n["uid"] in REMOVED_COMPARISONS:
            writes.append(dict(n, is_deleted=True))
    if writes:
        notes.append(f"{len(writes)} comparison value(s) renamed or removed")
    fields = []
    for f in repo.by_types([K.NT_MATCH_FIELD]):
        c = (f["properties"] or {}).get("Comparison")
        if c in REMOVED_COMPARISONS:
            fields.append(dict(f, properties={**f["properties"], "Comparison": REMOVED_COMPARISONS[c]}))
    if fields:
        notes.append(f"{len(fields)} Match Field(s): removed comparison replaced with Exact or Levenshtein")
    if writes or fields:
        repo.upsert(writes + fields, user)
    return notes


def _countries_table(repo, user: str) -> str | None:
    """The Countries enum as a Standardization Table (Alpha2, Alpha3, Name -> Name; Keep source), for the old
    Country step. Returns its UID, or None when there is no enum named Countries."""
    enum = repo.get(to_uid(2030))
    if not enum or enum["name"].lower() != "countries":
        enum = next((e for e in repo.by_kind("Enum") if e["name"].lower() == "countries"), None)
    if not enum:
        return None
    if K.NT_STANDARDIZATION_TABLE not in enum["types"]:
        names = {k for c in repo.children(enum["uid"], limit=50) for k in (c["properties"] or {})}
        p = {**(enum["properties"] or {}), "Standardization Columns": [c for c in ("Alpha2", "Alpha3") if c in names] + ["Name"],
             "Standardization Key": "Name", "Default Method": to_uid(1446), "Default Value": ""}
        repo.upsert([dict(enum, types=list(enum["types"]) + [K.NT_STANDARDIZATION_TABLE], properties=p)], user)
    return enum["uid"]


def _new_steps(step: str, countries) -> list[str]:
    from .matching.standardise import _PHONE
    s, low = step.strip(), step.strip().lower()
    if low == "drop legal forms":
        return [f"Drop words {to_uid(1450)}"]
    if low.startswith("replace "):
        return ["Regex " + s]
    if low == "email":
        return list(EMAIL_CLEANING)
    if low == "postcode":
        return list(POSTCODE_CLEANING)
    m = _PHONE.match(s)
    if m and low.startswith("phone"):
        return phone_cleaning(m.group(1) or "")
    if low == "country" and countries():
        return [f"Standardize {countries()}"]
    return [step]


def upgrade_cleaning(repo, user: str = "mdm_init") -> list[str]:
    """Cleaning steps of this release (idempotent): Drop legal forms -> Drop words <LegalForms enum>,
    Replace -> Regex Replace, and Phone, Email, Postcode -> Regex Replace steps, Country -> Standardize <Countries>."""
    if not repo.get(to_uid(1450)):
        return []
    cache = {}

    def countries():
        if "uid" not in cache:
            cache["uid"] = _countries_table(repo, user)
        return cache["uid"]

    writes = []
    for f in repo.by_types([K.NT_MATCH_FIELD]):
        steps = list((f["properties"] or {}).get("Cleaning") or [])
        new = [x for s in steps for x in _new_steps(s, countries)]
        if new != steps:
            writes.append(dict(f, properties={**f["properties"], "Cleaning": new}))
    if writes:
        repo.upsert(writes, user)
        return [f"{len(writes)} Match Field(s): cleaning steps rewritten to Drop words, Regex Replace and Standardize"]
    return []


def upgrade_matching(repo, user: str = "mdm_init") -> list[str]:
    """Matching became its own step before survivorship (idempotent).

    - Model › Rules accepts Match Rules; the Survivorship Rule NodeType loses Match Keys / Match Threshold.
    - Every Master Data node without a Match Rule gets one: a seeded Match Rule with the same sources, or a
      new "Exact keys only" rule whose exact-key Match Fields are the old Match Keys (mapped like their
      Property Rules). The old fuzzy name threshold is dropped.
    - Source records get the Golden UID of the golden record that lists them in _members, so the first
      (full) run keeps the existing golden UIDs by majority.
    """
    from .matching.spec import COMPARISON_BY_NAME, METHOD_EXACT, ROLE_BY_KEY
    notes, writes = [], {}

    def put(n):
        writes[n["uid"]] = n
        return n

    rules = repo.get(K.MODEL_RULES)
    if rules and K.NT_MATCH_RULE not in (rules.get("allowed_child_types") or []):
        put(dict(rules, allowed_child_types=list(rules.get("allowed_child_types") or []) + [K.NT_MATCH_RULE]))
    srt = repo.get(K.NT_SURVIVORSHIP_RULE)
    if srt:
        defs = (srt["properties"] or {}).get("property_defs") or []
        keep = [d for d in defs if d["name"] not in ("Match Keys", "Match Threshold")]
        if len(keep) != len(defs):
            put(dict(srt, properties={**srt["properties"], "property_defs": keep}))
    match_rules = repo.by_types([K.NT_MATCH_RULE])
    for m in repo.by_types([K.NT_MASTER_DATA]):
        mp = m["properties"] or {}
        sr = repo.get(mp.get("Survivorship Rule") or "")
        if not sr:
            continue
        srp = dict(sr["properties"] or {})
        if not mp.get("Match Rule"):
            same = next((r for r in match_rules if set((r["properties"] or {}).get("Sources") or [])
                         == set(srp.get("Sources") or [])), None)
            if same is None:
                prs = {(c["properties"] or {}).get("Property"): c for c in repo.children(sr["uid"])
                       if K.NT_PROPERTY_RULE in c["types"]}
                keys = [k for k in srp.get("Match Keys") or [] if k in prs]
                nums = repo.allocate(1 + len(keys))
                same = new_node(nums[0], f"{m['name']} match", [K.NT_MATCH_RULE], rules, icon="merge", max_child_depth=2,
                                allowed_child_types=[K.NT_MATCH_FIELD, K.NT_FUZZY_RULE, K.NT_FOLDER],
                                properties={"Sources": list(srp.get("Sources") or []), "Method": METHOD_EXACT,
                                            "Blocking Rules": [], "Link Threshold": 0.95, "Review Threshold": 0.70})
                put(same)
                for i, (k, num) in enumerate(zip(keys, nums[1:])):
                    put(new_node(num, k, [K.NT_MATCH_FIELD, K.NT_ORDERED], same, icon="hash", max_child_depth=0,
                                 sort_order=float(i + 1), properties={
                                     "Role": ROLE_BY_KEY["exact"], "Comparison": COMPARISON_BY_NAME["Exact"],
                                     "Cleaning": VAT_CLEANING if "vat" in k.lower() else ["Trim", "Upper-case", "Empty markers"],
                                     "Source Mapping": (prs[k]["properties"] or {}).get("Source Mapping") or ""}))
                match_rules.append(same)
                notes.append(f"{m['name']}: new Match Rule '{same['name']}' (exact keys: {', '.join(keys) or 'none'})")
            else:
                notes.append(f"{m['name']}: uses the Match Rule '{same['name']}'")
            put(dict(m, properties={**mp, "Match Rule": same["uid"]}))
        if "Match Keys" in srp or "Match Threshold" in srp:
            srp.pop("Match Keys", None)
            srp.pop("Match Threshold", None)
            put(dict(sr, properties=srp))
        # existing golden records -> Golden UID on their source records (only where none is set yet)
        owner = {}
        for g in repo.children(m["uid"]):
            for u in (g["properties"] or {}).get("_members") or []:
                owner[u] = g["uid"]
        if owner:
            recs = repo.get_many(list(owner))
            n = 0
            for u, r in recs.items():
                if not (r["properties"] or {}).get("Golden UID"):
                    put(dict(r, properties={**(r["properties"] or {}), "Golden UID": owner[u]}))
                    n += 1
            if n:
                notes.append(f"{m['name']}: Golden UID set on {n} source record(s) from the existing golden records")
    if writes:
        repo.upsert(list(writes.values()), user)
    return notes
