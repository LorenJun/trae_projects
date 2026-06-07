import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from okooo_mobile_access import available_mobile_profiles, cache_busted_okooo_url, mobile_headers, random_mobile_profile
from runtime.okooo_access import open_okooo_verification_breaker, read_okooo_verification_breaker, wait_for_okooo_market_slot


class OkoooMobileAccessTest(unittest.TestCase):
    def test_available_mobile_profiles_cover_multiple_device_pools(self):
        profiles = available_mobile_profiles()
        self.assertEqual(len(profiles), 500)
        self.assertEqual(len({p.profile_id for p in profiles}), 500)
        self.assertEqual(
            {p.device_pool_id for p in profiles},
            {"iphone_se", "iphone_12", "iphone_13_mini", "iphone_14_plus", "iphone_15_pro"},
        )

    def test_all_mobile_profiles_are_iphone_safari_variants(self):
        profiles = available_mobile_profiles()
        for profile in profiles:
            self.assertIn("(iPhone; CPU iPhone OS", profile.user_agent)
            self.assertIn("AppleWebKit/605.1.15", profile.user_agent)
            self.assertIn("Mobile/15E148 Safari/604.1", profile.user_agent)
            self.assertNotIn("Android", profile.user_agent)
            self.assertIn(profile.device_scale_factor, {2, 3})
            self.assertIn(
                profile.viewport,
                (
                    {"width": 375, "height": 667},
                    {"width": 390, "height": 844},
                    {"width": 375, "height": 812},
                    {"width": 428, "height": 926},
                    {"width": 393, "height": 852},
                ),
            )

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
        self.assertEqual(headers["X-Okooo-Mobile-Profile"], profile.profile_id)
        self.assertEqual(headers["X-Okooo-Mobile-Device-Pool"], profile.device_pool_id)

    def test_random_mobile_profile_uses_random_choice(self):
        profile = available_mobile_profiles()[3]
        with patch("okooo_mobile_access.random.choice", return_value=profile):
            selected = random_mobile_profile()
        self.assertEqual(selected.profile_id, profile.profile_id)

    def test_device_pool_has_multiple_versions(self):
        profiles = available_mobile_profiles()
        se_profiles = [profile for profile in profiles if profile.device_pool_id == "iphone_se"]
        self.assertEqual(len(se_profiles), 100)
        self.assertGreater(len({profile.user_agent for profile in se_profiles}), 50)

    def test_verification_breaker_opens_and_expires(self):
        with patch("runtime.okooo_access.time.time", side_effect=[1000.0, 1000.0, 2001.0]):
            opened = open_okooo_verification_breaker("/tmp/okooo_access_test", "1315851", "odds_family", ttl_seconds=10)
            active = read_okooo_verification_breaker("/tmp/okooo_access_test", "1315851", "odds_family")
            expired = read_okooo_verification_breaker("/tmp/okooo_access_test", "1315851", "odds_family")

        self.assertEqual(opened["match_id"], "1315851")
        self.assertTrue(active["open"])
        self.assertFalse(expired["open"])

    def test_wait_for_okooo_market_slot_returns_zero_when_no_wait_needed(self):
        with patch("runtime.okooo_access._load_runtime_json", return_value={"last_access_at": 1000.0}), patch(
            "runtime.okooo_access._save_runtime_json"
        ), patch("runtime.okooo_access.time.time", side_effect=[1002.0, 1002.0]), patch(
            "runtime.okooo_access.time.sleep"
        ) as mock_sleep:
            waited = wait_for_okooo_market_slot("/tmp/okooo_access_throttle")

        self.assertEqual(waited, 0.0)
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
