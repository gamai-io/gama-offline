import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gama_offline import cache, cbr, skills, pretriage, risk

# Three real goals from ~/gamacode/cases.db (ids 659, 660, 990).  Under the old
# "first six sorted unique words" intent key all three were "1 2 3 4 5 6".
REAL_CORPUS_COLLISION = (
    "U /tmp/multi napravi: (1) dohvati kurs EUR->BAM preko free_api, "
    "(2) dohvati naslov sa https://example.com, (3) napravi QR kod za "
    "https://example.com u /tmp/multi/qr.png, (4) napravi Word "
    "/tmp/multi/izvjestaj.docx sa kursom i naslovom, (5) napravi PDF "
    "/tmp/multi/izvjestaj.pdf, (6) provjeri sa ls -l da sva 3 fajla "
    "postoje i navedi velicine.",
    "Napravi provjeru flote u /tmp/multi: (1) pozovi fleet who, "
    "(2) pozovi hc who na kuca, (3) napravi screenshot dispecera, "
    "(4) napravi glasovnu poruku tts tekst flota provjerena u "
    "/tmp/multi/flota.mp3, (5) dohvati https://example.com, "
    "(6) zabiljezi kratku lekciju u memoriju (brain_write) o tome da je "
    "provjera prosla, (7) sve to sazi mi u /tmp/multi/flota.md i "
    "potvrdi velicine.",
    "CSV: a,b 1,2 3,4 5,6 Koliki je zbir kolone b? Odgovori samo brojem.",
)


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
    def test_risk(self):
        self.assertEqual(risk.risk_of("rm -rf /")["risk"], 0.9)
        self.assertEqual(risk.risk_of("Obriši sve fajlove")["risk"], 0.9)
        self.assertFalse(risk.risk_of("napravi ponudu")["blocked"])
    def test_pretriage(self):
        r = pretriage.decide("obrisi sve fajlove i formatiraj disk")
        self.assertTrue(r["skip_llm"])
        r2 = pretriage.decide("zdravo, kako si")
        self.assertFalse(r2["skip_llm"])
    def test_cache_roundtrip(self):
        cache._DB = os.path.join(self.tmp, "c.db"); cache._conn = None
        res = {"content": "hi there, this is a cached answer", "provider": "t", "trajectory": [], "ok": True}
        cache.put("s1", "zdravo", res)
        got = cache.get("s1", "zdravo")
        self.assertIsNotNone(got)
        self.assertIn("cached answer", str(got))
        self.assertIsNone(cache.get("s1", "drugo"))
    def test_cache_key_properties(self):
        """Key = dsc.cachekey: v2-tagged, stable, scoped, no plaintext.

        This asserts properties, not a digest, so the key could move hash
        function without rewriting the test.
        """
        k = cache._key("s1", "  Zdravo   Svijete ")
        self.assertTrue(k.startswith("v2:"))
        self.assertEqual(len(k), 3 + 64)
        self.assertRegex(k[3:], r"^[0-9a-f]{64}$")
        self.assertEqual(k, cache._key("s1", "zdravo svijete"))
        self.assertNotIn("zdravo", k)
        self.assertNotEqual(k, cache._key("s2", "zdravo svijete"))
        self.assertNotEqual(k, cache._key("", "zdravo svijete"))
        self.assertNotEqual(cache._key("s1", "zdravo"), cache._key("s1", "zdravo!"))
    def test_cache_key_delimiter_not_injectable(self):
        """Old `sid|text` framing collided here; JSON framing must not."""
        self.assertNotEqual(cache._key("a", "b|c"), cache._key("a|b", "c"))
    def test_cache_poison_not_served_for_other_request(self):
        cache._DB = os.path.join(self.tmp, "poison.db"); cache._conn = None
        res = {"content": "POISON answer that is long enough", "provider": "t"}
        cache.put("s1", "koliko ima kupaca", res)
        self.assertIsNotNone(cache.get("s1", "koliko ima kupaca"))
        self.assertIsNone(cache.get("s1", "koliko ima dobavljaca"))
        self.assertIsNone(cache.get("s2", "koliko ima kupaca"))
    def test_old_md5_rows_are_missed_not_served(self):
        """First run after the switch: old key space misses on purpose."""
        import hashlib, sqlite3, time
        cache._DB = os.path.join(self.tmp, "old.db"); cache._conn = None
        old = hashlib.md5(b"s1|zdravo").hexdigest()
        c = sqlite3.connect(cache._DB)
        c.execute("CREATE TABLE IF NOT EXISTS cache(k TEXT PRIMARY KEY, sid TEXT, q TEXT, "
                  "content TEXT, provider TEXT, ts REAL, hits INTEGER DEFAULT 0)")
        c.execute("INSERT INTO cache(k,sid,q,content,provider,ts,hits) VALUES(?,?,?,?,?,?,0)",
                  (old, "s1", "zdravo", "STALE md5 answer long enough", "t", time.time()))
        c.commit(); c.close()
        self.assertIsNone(cache.get("s1", "zdravo"))
        self.assertEqual(cache.stats()["entries"], 1)  # old row kept, just unreachable
    def test_cache_fresh_guard(self):
        self.assertTrue(cache.fresh("koliko kosta XHD211"))
        self.assertFalse(cache.cacheable({"say": "ok"}, "koliko kosta XHD211"))
    def test_erp_fresh_guard_v2(self):
        # ERP_FRESH_V2: stanje/cijene/zalihe se nikad ne smiju servirati iz kesa
        for q in ("stanje zaliha za 100011", "lager 100011", "koliko imamo na stanju",
                  "saldo kupca 211008", "dugovanja kupaca", "zadnja cijena XHD211",
                  "trenutna cijena mixera", "kolicina na skladistu"):
            self.assertTrue(cache.fresh(q), q)
        # a objasnjenja/procedure ostaju kesabilna
        for q in ("kako se kontira ulaz robe", "objasni sta je false success"):
            self.assertFalse(cache.fresh(q), q)

    def test_cbr(self):
        cbr.DB = os.path.join(self.tmp, "cases.db")
        cbr.add("napravi ponudu za kupca", [{"tool": "ponuda"}], ok=True)
        hits = cbr.find("napravi ponudu", k=1)
        self.assertTrue(hits)

    def test_cbr_intent_is_a_stable_v2_key(self):
        """Same goal -> same id, and the id leaks no goal text."""
        k = cbr._intent("Napravi ponudu za kupca")
        self.assertTrue(k.startswith("v2:"), k)
        self.assertEqual(len(k), 3 + 64)
        self.assertEqual(k, cbr._intent("napravi   PONUDU za kupca"))
        self.assertEqual(k, cbr._intent("Napravi ponudu za kupca"))
        self.assertNotIn("ponud", k)
        # word order is significant: a reordered goal is a different goal
        self.assertNotEqual(cbr._intent("posalji mail kupcu"),
                            cbr._intent("kupcu posalji mail"))

    def test_cbr_legacy_key_collided_on_real_corpus_goals(self):
        """Three real cases from ~/gamacode/cases.db shared the old intent.

        The old ``_intent`` was the first six sorted unique words, so these
        three unrelated multi-step goals all got ``"1 2 3 4 5 6"`` and were
        promoted as one recipe.  The new key must keep them apart.
        """
        import re

        def legacy_intent(goal):
            s = re.sub(r"[^\w\s]", " ", (goal or "").lower())
            w = [x for x in s.split() if x and x not in cbr.STOP]
            return " ".join(sorted(set(w))[:6])

        goals = REAL_CORPUS_COLLISION
        self.assertEqual(len({legacy_intent(g) for g in goals}), 1)
        self.assertEqual(legacy_intent(goals[0]), "1 2 3 4 5 6")
        self.assertEqual(len({cbr._intent(g) for g in goals}), 3)
        for g in goals:
            self.assertEqual(cbr._intent(g), cbr._intent(g))

    def test_cbr_promote_does_not_merge_distinct_goals(self):
        cbr.DB = os.path.join(self.tmp, "promote.db")
        for g in REAL_CORPUS_COLLISION:
            cbr.add(g, [{"tool": "x"}], ok=True)
        promoted = cbr.promote(min_ok=1)
        self.assertEqual(len(promoted), 3, promoted)
        self.assertEqual(cbr.stats()["cases"], 3)
    def test_skills(self):
        d = os.path.join(self.tmp, "skills", "gama-izvjestaj")
        os.makedirs(d)
        open(os.path.join(d, "SKILL.md"), "w").write("---\nname: gama-izvjestaj\ndescription: izvjestaj sa slikama i PDF\n---\n")
        skills.SKILLS_LIB = os.path.join(self.tmp, "skills"); skills._IDX = None
        b = skills.best("napravi izvjestaj sa slikama i pdf")
        self.assertEqual(b[0]["name"], "gama-izvjestaj")

if __name__ == "__main__": unittest.main()
