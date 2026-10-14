"""检查日报上传和分发结果；只打印环节，不把微信响应中的凭证带进公开日志。"""
import json
import sys


def failed_stages(response):
    if not isinstance(response, dict) or response.get("code") != 0:
        return ["日报上传"]
    return [label for field, label in (
        ("draftResult", "公众号草稿创建"),
        ("momentResult", "规划师朋友圈下发"),
    ) if str(response.get(field, "")).startswith("fail:")]


def main():
    try:
        stages = failed_stages(json.load(sys.stdin))
    except (ValueError, TypeError):
        stages = ["日报上传（服务端响应格式错误）"]
    if stages:
        print("::error::" + "、".join(stages) + "失败，请查看后台日志")
        return 1
    print("✓ 日报上传完成，分发结果无失败")
    return 0


if __name__ == "__main__":
    sys.exit(main())
