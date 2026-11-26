import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gama_offline import cache, cbr, skills, pretriage, risk

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
    def test_skills(self):
        d = os.path.join(self.tmp, "skills", "gama-izvjestaj")
        os.makedirs(d)
        open(os.path.join(d, "SKILL.md"), "w").write("---\nname: gama-izvjestaj\ndescription: izvjestaj sa slikama i PDF\n---\n")
        skills.SKILLS_LIB = os.path.join(self.tmp, "skills"); skills._IDX = None
        b = skills.best("napravi izvjestaj sa slikama i pdf")
        self.assertEqual(b[0]["name"], "gama-izvjestaj")

if __name__ == "__main__": unittest.main()
