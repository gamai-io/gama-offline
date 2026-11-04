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
    def test_cache_fresh_guard(self):
        self.assertTrue(cache.fresh("koliko kosta XHD211"))
        self.assertFalse(cache.cacheable({"say": "ok"}, "koliko kosta XHD211"))
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
