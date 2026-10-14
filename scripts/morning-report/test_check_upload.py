import unittest
from check_upload import failed_stages


class CheckUploadTest(unittest.TestCase):
    def test_upload_success_does_not_hide_draft_failure(self):
        self.assertEqual(failed_stages({"code": 0, "draftResult": "fail: 微信返回错误",
                                       "momentResult": "ok:123"}), ["公众号草稿创建"])

    def test_reports_both_delivery_failures(self):
        self.assertEqual(failed_stages({"code": 0, "draftResult": "fail: x",
                                       "momentResult": "fail: y"}),
                         ["公众号草稿创建", "规划师朋友圈下发"])

    def test_preserves_intentional_gates(self):
        self.assertEqual(failed_stages({"code": 0, "draftResult": "gated: 未启用",
                                       "momentResult": "skipped"}), [])

    def test_bad_upload_response(self):
        for response in (None, [], {"code": 500}, {"code": "0"}):
            self.assertEqual(failed_stages(response), ["日报上传"])


if __name__ == "__main__":
    unittest.main()
