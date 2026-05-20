import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from okooo_mobile_access import available_mobile_profiles, cache_busted_okooo_url, mobile_headers, random_mobile_profile


class OkoooMobileAccessTest(unittest.TestCase):
    def test_available_mobile_profiles_has_hundred_variants(self):
        profiles = available_mobile_profiles()
        self.assertEqual(len(profiles), 100)
        self.assertEqual(len({p.profile_id for p in profiles}), 100)

    def test_all_mobile_profiles_are_iphone_safari_variants(self):
        profiles = available_mobile_profiles()
        for profile in profiles:
            self.assertIn("(iPhone; CPU iPhone OS", profile.user_agent)
            self.assertIn("AppleWebKit/605.1.15", profile.user_agent)
            self.assertIn("Mobile/15E148 Safari/604.1", profile.user_agent)
            self.assertNotIn("Android", profile.user_agent)
            self.assertEqual(profile.device_scale_factor, 3)
            self.assertEqual(profile.viewport, {"width": 1080, "height": 720})

    def test_cache_busted_okooo_url_adds_mobile_markers(self):
        raw = "https://m.okooo.com/match/odds.php?MatchID=1302914"
        profile = available_mobile_profiles()[0]
        updated = cache_busted_okooo_url(raw, profile=profile)
        parts = urlsplit(updated)
        query = parse_qs(parts.query)

        self.assertEqual(parts.scheme, "https")
        self.assertEqual(parts.netloc, "m.okooo.com")
        self.assertEqual(query["MatchID"], ["1302914"])
        self.assertEqual(query[profile.cache_key], [profile.profile_id])
        self.assertTrue(query[profile.ts_key][0].isdigit())
        self.assertTrue(query[profile.nonce_key][0].startswith(profile.nonce_prefix))

    def test_cache_busted_okooo_url_keeps_non_okooo_urls_unchanged(self):
        raw = "https://example.com/path?a=1"
        self.assertEqual(cache_busted_okooo_url(raw), raw)

    def test_mobile_headers_include_user_agent_and_no_cache(self):
        profile = available_mobile_profiles()[0]
        headers = mobile_headers(profile=profile)
        self.assertEqual(headers["User-Agent"], profile.user_agent)
        self.assertEqual(headers["Cache-Control"], profile.cache_control)
        self.assertEqual(headers["Pragma"], "no-cache")
        self.assertEqual(headers["Referer"], "https://m.okooo.com/")

    def test_random_mobile_profile_uses_random_choice(self):
        profile = available_mobile_profiles()[3]
        with patch("okooo_mobile_access.random.choice", return_value=profile):
            selected = random_mobile_profile()
        self.assertEqual(selected.profile_id, profile.profile_id)


if __name__ == "__main__":
    unittest.main()
