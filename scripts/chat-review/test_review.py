"""复盘失败状态回归测试；所有导出、AI 调用和上传均为 mock，不发送真实消息。"""
import contextlib
import copy
import io
import json
import unittest
from unittest.mock import Mock, patch

import review


def planner(vx):
    return {"vxId": vx, "name": "测试规划师" + vx,
            "customers": [{"today": [{}] * 6}], "phoneOnly": [], "coverage": []}


class ReviewRunTest(unittest.TestCase):
    def run_review(self, people, failures=(), response=None):
        result = {"overview": "测试", "improve": [], "good": [], "tomorrow": [], "push": "测试"}
        export = Mock()
        export.json.return_value = {"code": 0, "planners": []}
        upload = Mock()
        upload.json.return_value = response if response is not None else {"code": 0, "sent": len(people) - len(failures)}

        def analyze(_system, prompt, **_kwargs):
            if prompt in failures:
                raise RuntimeError("模拟接口异常")
            return copy.deepcopy(result), {}

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.multiple(review, TOKEN="fake-token", MODE="run", TEST_VXID="", ONLY=[],
                                              WINDOW_END="2026-10-15", FORCE=False))
            stack.enter_context(patch.dict("os.environ", {"GITHUB_REPOSITORY": "test/repo", "GITHUB_RUN_ID": "123"}))
            stack.enter_context(patch.object(review, "workday_status", return_value=(True, "工作日")))
            stack.enter_context(patch.object(review, "window_days", return_value=["2026-10-14"]))
            stack.enter_context(patch.object(review, "merge_exports", return_value=copy.deepcopy(people)))
            stack.enter_context(patch.object(review, "drop_broadcast_only"))
            stack.enter_context(patch.object(review, "trim_to_fit"))
            stack.enter_context(patch.object(review.tempfile, "mkdtemp", return_value="/unused"))
            stack.enter_context(patch.object(review, "fetch_images", return_value=("/unused", 0)))
            stack.enter_context(patch.object(review, "build_user_prompt", side_effect=lambda p, *_: p["vxId"]))
            stack.enter_context(patch.object(review, "render_page", return_value="<p>测试</p>"))
            stack.enter_context(patch.object(review, "call_deepseek", side_effect=analyze))
            stack.enter_context(patch.object(review.requests, "get", return_value=export))
            post = stack.enter_context(patch.object(review.requests, "post", return_value=upload))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            try:
                review.main()
                code = 0
            except SystemExit as e:
                code = e.code
            payload = json.loads(post.call_args.kwargs["data"]["payload"]) if post.called else None
            return code, payload, output.getvalue()

    def test_one_of_four_success_still_uploads_but_fails_run(self):
        code, payload, log = self.run_review([planner(v) for v in "abcd"], failures="bcd")
        self.assertEqual(1, code)  # 即使旧后台返回 code=0，也不能报成功
        self.assertEqual(["a"], [p["vxId"] for p in payload["planners"]])
        self.assertEqual(["b", "c", "d"], [p["vxId"] for p in payload["failedPlanners"]])
        self.assertEqual("https://github.com/test/repo/actions/runs/123", payload["runUrl"])
        self.assertNotIn("测试规划师", log)  # 公开日志只放账号标识，不放姓名或复盘内容

    def test_all_failed_reports_failure_without_fabricated_results(self):
        code, payload, _ = self.run_review([planner("a")], failures="a")
        self.assertEqual(1, code)
        self.assertEqual([], payload["planners"])
        self.assertEqual("a", payload["failedPlanners"][0]["vxId"])

    def test_all_success(self):
        code, payload, _ = self.run_review([planner("a"), planner("b")])
        self.assertEqual(0, code)
        self.assertEqual([], payload["failedPlanners"])
        self.assertEqual(2, len(payload["planners"]))

    def test_no_eligible_people_skips_without_failure(self):
        code, payload, _ = self.run_review([dict(planner("a"), customers=[])])
        self.assertEqual(0, code)
        self.assertIsNone(payload)

    def test_partial_delivery_and_upload_errors_fail_run(self):
        for response in ({"code": 0, "sent": 0}, {"code": 500, "sent": 1}, {"code": 0}):
            with self.subTest(response=response):
                code, _, _ = self.run_review([planner("a")], response=response)
                self.assertEqual(1, code)

    def test_ping_mode_does_not_run_review(self):
        with patch.object(review, "MODE", "ping"), patch.object(review, "ping") as ping:
            review.main()
            ping.assert_called_once_with()


class DeepSeekResponseTest(unittest.TestCase):
    def test_missing_choices_retries_without_logging_raw_response(self):
        resp = Mock()
        resp.json.return_value = {"error": {"message": "私密响应内容"}}
        with patch.object(review, "DEEPSEEK_KEY", "fake-key"), \
                patch.object(review.requests, "post", return_value=resp) as post, \
                patch.object(review.time, "sleep"), contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaisesRegex(RuntimeError, "缺少有效 choices"):
                review.call_deepseek("system", "user")
            self.assertEqual(3, post.call_count)
            self.assertNotIn("私密响应内容", out.getvalue())


if __name__ == "__main__":
    unittest.main()
