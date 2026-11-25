"""Generated demo source data: customers (3 sources), suppliers (2), employees (2).

Deterministic (fixed random seeds), so every deployment gets the same rows and the same keys.
All names, VAT IDs, IBANs, phone numbers and e-mail domains are invented; mail domains use the
reserved `.example` TLD. The rows deliberately contain what real sources contain: the same company
spelled differently per source, missing VAT IDs, local vs. English city names, typos, stale values,
a few duplicates inside one source.

    customers  1 000 companies in 7 countries → SAP kna1 (~74%), Salesforce account (~58%), webshop (~35%)
    suppliers    200 companies                 → SAP lfa1 (~85%), Coupa suppliers (~65%)
    employees    300 people                    → Workday workers (all), Active Directory users (active ones)
"""

from __future__ import annotations

import difflib
import random
import re
import unicodedata
from functools import lru_cache

# ----------------------------------------------------------------------------- geography
COUNTRIES = {  # name: (alpha2, legal forms, VAT prefix, VAT digits, phone prefix, area codes, cities)
    "Germany": ("DE", ["GmbH", "GmbH", "AG", "GmbH & Co. KG"], "DE", 9, "+49", ["30", "89", "40", "221", "69"],
                ["Berlin", "München", "Hamburg", "Köln", "Frankfurt am Main", "Stuttgart", "Leipzig"]),
    "Hungary": ("HU", ["Kft.", "Kft.", "Zrt."], "HU", 8, "+36", ["1", "52", "62", "96", "72"],
                ["Budapest", "Debrecen", "Szeged", "Győr", "Pécs", "Székesfehérvár"]),
    "Austria": ("AT", ["GmbH", "GmbH", "AG"], "ATU", 8, "+43", ["1", "316", "732", "662"],
                ["Wien", "Graz", "Linz", "Salzburg", "Innsbruck"]),
    "Switzerland": ("CH", ["AG", "AG", "SA", "GmbH"], "CHE", 9, "+41", ["44", "61", "31", "22", "41"],
                    ["Zürich", "Basel", "Bern", "Genève", "Zug", "Luzern"]),
    "Czechia": ("CZ", ["s.r.o.", "s.r.o.", "a.s."], "CZ", 8, "+420", ["2", "5", "59"],
                ["Praha", "Brno", "Ostrava", "Plzeň"]),
    "Slovakia": ("SK", ["s.r.o.", "a.s."], "SK", 10, "+421", ["2", "55", "41"],
                 ["Bratislava", "Košice", "Žilina", "Nitra"]),
    "Romania": ("RO", ["S.R.L.", "S.R.L.", "S.A."], "RO", 8, "+40", ["21", "264", "256"],
                ["București", "Cluj-Napoca", "Timișoara", "Brașov"]),
}
CUSTOMER_MIX = {"Germany": 280, "Hungary": 190, "Austria": 130, "Switzerland": 120, "Czechia": 120,
                "Slovakia": 70, "Romania": 90}
EXONYM = {"München": "Munich", "Köln": "Cologne", "Frankfurt am Main": "Frankfurt", "Wien": "Vienna",
          "Zürich": "Zurich", "Genève": "Geneva", "Luzern": "Lucerne", "Praha": "Prague",
          "București": "Bucharest", "Plzeň": "Pilsen"}

PEOPLE = {  # country: (male first, female first, male last, female last)
    "Hungary": (["László", "Gábor", "Péter", "Balázs", "Tamás", "Zoltán", "Bence", "Ádám", "Dávid", "Márton"],
                ["Anna", "Eszter", "Katalin", "Zsófia", "Réka", "Dóra", "Judit", "Petra", "Noémi", "Lilla"],
                ["Nagy", "Kovács", "Tóth", "Szabó", "Horváth", "Varga", "Kiss", "Molnár", "Németh", "Farkas",
                 "Balogh", "Papp", "Takács", "Juhász", "Lakatos", "Mészáros"], None),
    "Germany": (["Thomas", "Michael", "Stefan", "Jonas", "Lukas", "Felix", "Andreas", "Tobias", "Jan", "Florian"],
                ["Anna", "Laura", "Julia", "Sophie", "Katrin", "Lena", "Sarah", "Lisa", "Hannah", "Miriam"],
                ["Müller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer", "Wagner", "Becker", "Hoffmann",
                 "Schulz", "Koch", "Richter", "Wolf", "Neumann", "Zimmermann", "Krüger"], None),
    "Austria": (["Lukas", "Florian", "Markus", "Christoph", "Dominik", "Philipp", "Matthias", "Bernhard"],
                ["Katharina", "Lisa", "Sabine", "Theresa", "Verena", "Magdalena", "Elisabeth", "Johanna"],
                ["Gruber", "Huber", "Bauer", "Wagner", "Pichler", "Steiner", "Moser", "Mayer", "Hofer", "Leitner",
                 "Berger", "Fuchs", "Eder"], None),
    "Switzerland": (["Noah", "Luca", "Elias", "Matteo", "Simon", "David", "Marco", "Reto", "Urs", "Beat"],
                    ["Lea", "Nina", "Sara", "Mia", "Laura", "Andrea", "Corinne", "Martina", "Seraina", "Chiara"],
                    ["Müller", "Meier", "Schmid", "Keller", "Weber", "Huber", "Schneider", "Steiner", "Gerber",
                     "Brunner", "Baumann", "Frei", "Rossi", "Favre"], None),
    "Czechia": (["Jan", "Jakub", "Tomáš", "Petr", "Martin", "Lukáš", "Ondřej", "Pavel", "Jiří", "David"],
                ["Jana", "Tereza", "Eva", "Petra", "Lucie", "Kateřina", "Veronika", "Markéta", "Barbora", "Hana"],
                ["Novák", "Svoboda", "Novotný", "Dvořák", "Černý", "Procházka", "Kučera", "Veselý", "Horák",
                 "Pokorný", "Marek", "Král"],
                ["Nováková", "Svobodová", "Novotná", "Dvořáková", "Černá", "Procházková", "Kučerová", "Veselá",
                 "Horáková", "Pokorná", "Marková", "Králová"]),
    "Slovakia": (["Ján", "Peter", "Martin", "Michal", "Tomáš", "Lukáš", "Marek", "Juraj"],
                 ["Mária", "Zuzana", "Katarína", "Lucia", "Martina", "Jana", "Simona", "Ivana"],
                 ["Horváth", "Kováč", "Varga", "Baláž", "Szabó", "Molnár", "Lukáč", "Polák", "Šimko", "Hudák"],
                 ["Horváthová", "Kováčová", "Vargová", "Balážová", "Szabóová", "Molnárová", "Lukáčová", "Poláková",
                  "Šimková", "Hudáková"]),
    "Romania": (["Andrei", "Alexandru", "Mihai", "Ionuț", "Bogdan", "Radu", "Cristian", "Vlad", "Florin", "Sorin"],
                ["Maria", "Elena", "Ioana", "Andreea", "Ana", "Cristina", "Alexandra", "Diana", "Raluca", "Irina"],
                ["Popescu", "Ionescu", "Popa", "Pop", "Radu", "Dumitru", "Stan", "Stoica", "Gheorghe", "Matei",
                 "Ciobanu", "Rusu"], None),
}

_START = ["Al", "Ber", "Cor", "Dan", "Eri", "Fal", "Gra", "Hel", "Ist", "Jor", "Kal", "Lum", "Mar", "Nor", "Or",
          "Pra", "Quin", "Ros", "Sol", "Tor", "Ul", "Val", "Wen", "Xan", "Zel", "Brio", "Cel", "Dor", "Fen", "Gal",
          "Har", "Lin", "Mer", "Nev", "Ost", "Pol", "Rav", "Sar", "Tes", "Vir", "Ad", "Bel", "Cas", "Del", "Ems",
          "Fir", "Gol", "Kor", "Lev", "Mon"]
_END = ["ada", "ander", "ano", "aris", "ax", "ella", "enta", "eon", "ex", "ia", "ica", "ion", "is", "ium", "ix",
        "on", "ora", "os", "um", "us", "vita", "wood", "berg", "stein", "feld", "tec", "nova", "lux", "mont", "dal"]
CUSTOMER_SECTORS = ["Logistik", "Technik", "Foods", "Pharma", "Systems", "Bau", "Energie", "Textil", "Media",
                    "Consulting", "Metall", "Automotive", "Agrar", "Medical", "Data", "Print", "Glas", "Holz",
                    "Chemie", "Optik", "Handel", "Elektro", "Facility", "Retail", "Kunststoff", "Maschinenbau"]
SUPPLIER_SECTORS = ["Packaging", "Steel", "Plastics", "Freight", "Components", "Castings", "Motors", "Batteries",
                    "Fasteners", "Electronics", "Rubber", "Cables", "Coatings", "Labels", "Pallets", "Bearings"]
COMMODITIES = {"Packaging": "Packaging", "Steel": "Raw materials", "Plastics": "Raw materials",
               "Freight": "Logistics services", "Components": "Mechanical parts", "Castings": "Mechanical parts",
               "Motors": "Electrical parts", "Batteries": "Electrical parts", "Fasteners": "Mechanical parts",
               "Electronics": "Electrical parts", "Rubber": "Raw materials", "Cables": "Electrical parts",
               "Coatings": "Chemicals", "Labels": "Packaging", "Pallets": "Packaging", "Bearings": "Mechanical parts"}


def ascii_(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", ascii_(s).lower()).strip("-")


def _unique_names(rng: random.Random, per_country: dict[str, int], sectors: list[str], used: set[str]) -> list[tuple]:
    """(country, base name) pairs; two names in one country never look alike (fuzzy ratio < 0.7),
    so the demo matching only merges what really is the same company."""
    out = []
    for country, n in per_country.items():
        taken: list[str] = []
        while len(taken) < n:
            base = f"{rng.choice(_START)}{rng.choice(_END)} {rng.choice(sectors)}"
            low = base.lower()
            if base in used:
                continue
            sm = difflib.SequenceMatcher(None, low, "")
            ok = True
            for t in taken:
                sm.set_seq2(t)
                if sm.real_quick_ratio() >= 0.7 and sm.quick_ratio() >= 0.7 and sm.ratio() >= 0.7:
                    ok = False
                    break
            if ok:
                taken.append(low)
                used.add(base)
                out.append((country, base))
    return out


def _vat(rng, country):
    _, _, pre, digits, *_ = COUNTRIES[country]
    return pre + "".join(str(rng.randint(0, 9)) for _ in range(digits))


def _phone(rng, country, style=0, city=None, num=None):
    pre, areas, cities = COUNTRIES[country][4], COUNTRIES[country][5], COUNTRIES[country][6]
    a = areas[cities.index(city) % len(areas)] if city in cities else rng.choice(areas)
    num = num or f"{rng.randint(100, 999)}{rng.randint(1000, 9999)}"
    if style == 1:
        return f"({pre}) {a}-{num[:3]}-{num[3:]}"
    if style == 2:
        return f"0{a} {num}"
    return f"{pre} {a} {num}"


def _iban(rng, country):
    a2 = COUNTRIES[country][0]
    length = {"DE": 18, "HU": 24, "AT": 16, "CH": 17, "CZ": 20, "SK": 20, "RO": 20}[a2]
    bban = "".join(str(rng.randint(0, 9)) for _ in range(length))
    if a2 == "RO":
        bban = "BANK" + bban[4:]
    digits = "".join(str(int(c, 36)) for c in bban + a2 + "00")
    check = 98 - int(digits) % 97
    return f"{a2}{check:02d}{bban}"


def _person(rng, country):
    mf, ff, ml, fl = PEOPLE[country]
    if rng.random() < 0.5:
        return rng.choice(mf), rng.choice(ml)
    return rng.choice(ff), rng.choice(fl or ml)


def _typo(rng, s):
    letters = [i for i in range(1, len(s) - 2) if s[i].isalpha() and s[i + 1].isalpha()]
    if not letters:
        return s
    i = rng.choice(letters)
    return s[:i] + s[i + 1] + s[i] + s[i + 2:]


def _legal_variant(rng, legal):
    return {"GmbH": rng.choice(["Gmbh", "GMBH", "GmbH"]), "Kft.": "Kft", "Zrt.": "ZRt.", "s.r.o.": "s. r. o.",
            "S.R.L.": "SRL", "AG": "AG", "SA": "S.A."}.get(legal, legal)


# ----------------------------------------------------------------------------- customers
@lru_cache(maxsize=1)
def customers() -> dict:
    rng = random.Random(20261116)
    used: set[str] = set()
    companies = []
    for country, base in _unique_names(rng, CUSTOMER_MIX, CUSTOMER_SECTORS, used):
        legal = rng.choice(COUNTRIES[country][1])
        first, last = _person(rng, country)
        dom = slug(base) + ".example"
        companies.append({  # noqa: E501
            "country": country, "base": base, "legal": legal, "city": rng.choice(COUNTRIES[country][6]),
            "vat": _vat(rng, country), "num": f"{rng.randint(100, 999)}{rng.randint(1000, 9999)}", "generic": f"{rng.choice(['info', 'office', 'sales'])}@{dom}",
            "personal": f"{slug(first)}.{slug(last)}@{dom}", "contact": f"{first} {last}",
        })
    kna1, account, shop = [], [], []
    kunnr, sf, cid = 200000, 0, 500000
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    for c in companies:
        name = f"{c['base']} {c['legal']}"
        in_sap, in_sf, in_shop = rng.random() < 0.74, rng.random() < 0.58, rng.random() < 0.35
        if not (in_sap or in_sf or in_shop):
            in_sap = True
        if in_sap:
            for dup in range(2 if rng.random() < 0.02 else 1):  # a duplicate sold-to account now and then
                kunnr += rng.randint(1, 9)
                n1 = name.upper() if rng.random() < 0.1 else name
                kna1.append([f"{kunnr:010d}", n1[:35], c["country"], c["city"],
                             c["vat"] if rng.random() < 0.86 and not dup else "",
                             _phone(rng, c["country"], 0, c["city"], c["num"]) if rng.random() < 0.8 else "",
                             c["generic"] if rng.random() < 0.6 else ""])
        if in_sf:
            sf += rng.randint(1, 40)
            x, sid = sf * 7919 + 9_000_000, ""
            for _ in range(6):
                x, r = divmod(x, 62)
                sid = alphabet[r] + sid
            sid = "0018X0000" + sid
            n2 = f"{c['base']} {_legal_variant(rng, c['legal'])}"
            if rng.random() < 0.05:
                n2 = _typo(rng, n2)
            vat = c["vat"] if rng.random() < 0.7 else ""
            if vat and rng.random() < 0.15:
                vat = vat[:2] + " " + vat[2:5] + " " + vat[5:]
            account.append([sid, n2, EXONYM.get(c["city"], c["city"]), c["country"], vat,
                            c["personal"] if rng.random() < 0.55 else (c["generic"] if rng.random() < 0.5 else ""),
                            _phone(rng, c["country"], 1, c["city"], c["num"]) if rng.random() < 0.7 else ""])
        if in_shop:
            cid += rng.randint(1, 30)
            n3 = c["base"] if rng.random() < 0.3 else name
            shop.append([f"C-{cid}", n3, c["contact"], c["personal"] if rng.random() < 0.85 else c["generic"],
                         _phone(rng, c["country"], 2, c["city"], c["num"]) if rng.random() < 0.5 else "",
                         rng.choice([c["city"], EXONYM.get(c["city"], c["city"])]), c["country"],
                         c["vat"] if rng.random() < 0.45 else "",
                         f"20{rng.randint(19, 26)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"])
    return {"kna1": kna1, "account": account, "shop": shop, "companies": len(companies)}


# ----------------------------------------------------------------------------- suppliers
@lru_cache(maxsize=1)
def suppliers() -> dict:
    rng = random.Random(4711)
    mix = {"Germany": 55, "Hungary": 45, "Austria": 25, "Czechia": 25, "Slovakia": 15, "Romania": 20, "Switzerland": 15}
    lfa1, coupa = [], []
    lifnr, cid = 300000, 1000
    terms = [("Z014", "Net 14"), ("Z030", "Net 30"), ("Z045", "Net 45"), ("Z060", "Net 60")]
    for country, base in _unique_names(rng, mix, SUPPLIER_SECTORS, set()):
        legal = rng.choice(COUNTRIES[country][1])
        name = f"{base} {legal}"
        city, vat = rng.choice(COUNTRIES[country][6]), _vat(rng, country)
        num = f"{rng.randint(100, 999)}{rng.randint(1000, 9999)}"
        dom = slug(base) + ".example"
        sap_t, coupa_t = rng.choice(terms), rng.choice(terms)
        if rng.random() < 0.7:
            coupa_t = sap_t  # mostly agreed, sometimes renegotiated in Coupa
        in_sap, in_coupa = rng.random() < 0.85, rng.random() < 0.65
        if not (in_sap or in_coupa):
            in_sap = True
        if in_sap:
            lifnr += rng.randint(1, 9)
            lfa1.append([f"{lifnr:010d}", name.upper() if rng.random() < 0.15 else name, country, city,
                         vat if rng.random() < 0.9 else "", _phone(rng, country, 0, city, num) if rng.random() < 0.8 else "",
                         _iban(rng, country), sap_t[0]])
        if in_coupa:
            cid += rng.randint(1, 12)
            sector = base.split(" ", 1)[1]
            coupa.append([str(cid), f"{base} {_legal_variant(rng, legal)}", country,
                          EXONYM.get(city, city), vat if rng.random() < 0.75 else "",
                          f"{rng.choice(['ar', 'accounts', 'orders'])}@{dom}", _phone(rng, country, 1, city, num),
                          coupa_t[1], COMMODITIES.get(sector, "Other")])
    return {"lfa1": lfa1, "coupa": coupa}


# ----------------------------------------------------------------------------- employees
DEPARTMENTS = {
    "Finance": ["Accountant", "Controller", "Financial Analyst", "Treasury Specialist"],
    "Human Resources": ["HR Business Partner", "Recruiter", "Payroll Specialist"],
    "Sales": ["Account Manager", "Key Account Manager", "Inside Sales Representative", "Sales Engineer"],
    "Marketing": ["Marketing Manager", "Content Specialist", "Product Marketing Manager"],
    "IT": ["Software Engineer", "Data Engineer", "IT Support Specialist", "Solution Architect"],
    "Operations": ["Production Planner", "Shift Supervisor", "Quality Engineer", "Maintenance Technician"],
    "Procurement": ["Buyer", "Category Manager", "Supplier Quality Engineer"],
    "Logistics": ["Warehouse Coordinator", "Transport Planner", "Logistics Analyst"],
    "R&D": ["Mechanical Engineer", "Electrical Engineer", "Test Engineer", "Product Designer"],
    "Customer Service": ["Service Agent", "Service Technician", "Team Lead Service"],
}
OFFICES = {"Hungary": ["Budapest HQ", "Debrecen Plant"], "Germany": ["München Sales Office", "Hamburg Warehouse"],
           "Austria": ["Wien Office"], "Czechia": ["Brno R&D Center"], "Slovakia": ["Bratislava Office"],
           "Romania": ["Cluj-Napoca Service Center"], "Switzerland": ["Zürich Office"]}
EMP_MIX = {"Hungary": 130, "Germany": 55, "Austria": 25, "Czechia": 35, "Slovakia": 20, "Romania": 25, "Switzerland": 10}


@lru_cache(maxsize=1)
def employees() -> dict:
    rng = random.Random(1848)
    people = []
    for country, n in EMP_MIX.items():
        for _ in range(n):
            people.append(country)
    rng.shuffle(people)
    workers, ad = [], []
    heads: dict[str, str] = {}
    seen_logins: set[str] = set()
    for i, country in enumerate(people):
        eid = f"E{10001 + i}"
        first, last = _person(rng, country)
        if i == 0:
            dept, title, manager = "Management", "Chief Executive Officer", ""
        elif len(heads) < len(DEPARTMENTS) and i <= len(DEPARTMENTS):
            dept = list(DEPARTMENTS)[i - 1]
            title, manager = f"Head of {dept}", "E10001"
            heads[dept] = eid
        else:
            dept = rng.choice(list(DEPARTMENTS))
            title, manager = rng.choice(DEPARTMENTS[dept]), heads[dept]
        status = "Terminated" if rng.random() < 0.05 and i > 20 else "Active"
        hire = f"20{rng.randint(8, 26):02d}-{rng.randint(1, 12):02d}-{rng.choice(['01', '15'])}"
        office = rng.choice(OFFICES[country])
        city = office.split(" ")[0]
        workers.append([eid, f"{first} {last}", first, last, title, dept, f"CC-{list(DEPARTMENTS).index(dept) + 100 if dept in DEPARTMENTS else 100}",
                        country, office, hire, manager, status])
        if status == "Terminated" and rng.random() < 0.7:
            continue  # disabled and removed from AD
        if hire >= "2026-10" and rng.random() < 0.6:
            continue  # new joiner, account not created yet
        login = slug(first)[0] + slug(last).replace("-", "")
        while login in seen_logins:
            login += str(rng.randint(1, 9))
        seen_logins.add(login)
        stale = rng.random() < 0.1
        ad_dept = rng.choice(list(DEPARTMENTS)) if stale and dept in DEPARTMENTS else dept
        display = f"{last}, {first}" if country in ("Germany", "Austria", "Switzerland") else f"{first} {last}"
        ad.append([login, eid, display, f"{slug(first)}.{slug(last)}@torvik-group.example",
                   _phone(rng, country, 0, city) if rng.random() < 0.85 else "", ad_dept, title if not stale else "", office])
    return {"workers": workers, "ad": ad}


# ----------------------------------------------------------------------------- source tables
def source_tables() -> dict:
    c, s, e = customers(), suppliers(), employees()
    return {
        "src_sap_kna1": {"columns": ["KUNNR", "NAME1", "LAND1", "ORT01", "STCEG", "TELF1", "SMTP_ADDR"], "rows": c["kna1"]},
        "src_sfdc_account": {"columns": ["Id", "Name", "BillingCity", "BillingCountry", "VAT_Number__c", "Email__c", "Phone"],
                             "rows": c["account"]},
        "src_shop_customers": {"columns": ["customer_id", "company", "contact_name", "email", "phone", "city", "country",
                                           "vat_id", "registered_at"], "rows": c["shop"]},
        "src_sap_lfa1": {"columns": ["LIFNR", "NAME1", "LAND1", "ORT01", "STCEG", "TELF1", "IBAN", "ZTERM"], "rows": s["lfa1"]},
        "src_coupa_suppliers": {"columns": ["id", "name", "country", "city", "tax_id", "primary_email", "phone",
                                            "payment_terms", "commodity"], "rows": s["coupa"]},
        "src_workday_workers": {"columns": ["Employee_ID", "Full_Name", "First_Name", "Last_Name", "Business_Title", "Department",
                                            "Cost_Center", "Country", "Location", "Hire_Date", "Manager_ID",
                                            "Worker_Status"], "rows": e["workers"]},
        "src_ad_users": {"columns": ["sAMAccountName", "employeeID", "displayName", "mail", "telephoneNumber",
                                     "department", "title", "physicalDeliveryOfficeName"], "rows": e["ad"]},
    }
