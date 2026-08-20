"""模块三单测：B 站链接校验/媒体文件工具（纯函数，无网络；b23 短链走 monkeypatch）。"""
import httpx
import pytest

from app.services import video_download as vd


# ---------------- 链接校验 ----------------

class TestParseVideoRef:
    def test_bv_full_url(self):
        assert vd.parse_video_ref(
            "https://www.bilibili.com/video/BV1rpWjevEip"
        ) == ("BV1rpWjevEip", None)

    def test_bv_with_p(self):
        assert vd.parse_video_ref(
            "https://www.bilibili.com/video/BV1rpWjevEip?p=3&t=60"
        ) == ("BV1rpWjevEip", 3)

    def test_bv_embedded_in_text(self):
        # BV 号出现在任意文本中也能提取（分享文案场景）
        assert vd.parse_video_ref("【科普】BV1rpWjevEip 这期讲了 MLX")[0] == "BV1rpWjevEip"

    def test_b23_short_link(self, monkeypatch):
        class FakeResp:
            url = "https://www.bilibili.com/video/BV1rpWjevEip?p=2"

        monkeypatch.setattr(vd.httpx, "get", lambda *a, **kw: FakeResp())
        assert vd.parse_video_ref("https://b23.tv/abcdEF") == ("BV1rpWjevEip", 2)

    def test_b23_redirect_without_bv(self, monkeypatch):
        class FakeResp:
            url = "https://www.bilibili.com/"

        monkeypatch.setattr(vd.httpx, "get", lambda *a, **kw: FakeResp())
        with pytest.raises(vd.VideoDownloadError, match="BV"):
            vd.parse_video_ref("https://b23.tv/abcdEF")

    def test_b23_network_error(self, monkeypatch):
        def boom(*a, **kw):
            raise httpx.HTTPError("网络异常")

        monkeypatch.setattr(vd.httpx, "get", boom)
        with pytest.raises(vd.VideoDownloadError, match="短链解析失败"):
            vd.parse_video_ref("https://b23.tv/abcdEF")

    def test_not_bilibili(self):
        with pytest.raises(vd.VideoDownloadError, match="不是 B 站"):
            vd.parse_video_ref("https://example.com/watch?v=123")


# ---------------- 媒体文件工具 ----------------

class TestMediaHelpers:
    def test_build_video_url(self):
        assert vd.build_video_url(
            "BV1rpWjevEip", None
        ) == "https://www.bilibili.com/video/BV1rpWjevEip"
        assert vd.build_video_url("BV1rpWjevEip", 2).endswith("?p=2")

    def test_media_name(self):
        assert vd.media_name("BV1rpWjevEip", None, "m4a") == "BV1rpWjevEip.m4a"
        assert vd.media_name("BV1rpWjevEip", 2, "m4a") == "BV1rpWjevEip_p2.m4a"

    def test_find_subtitle_cc_priority(self, tmp_path):
        (tmp_path / "BVxx.zh-Hans.vtt").write_text("CC")
        (tmp_path / "BVxx.ai-zh.vtt").write_text("AI")
        mode, f = vd._find_subtitle(tmp_path)
        assert mode == "CC"
        assert f.name == "BVxx.zh-Hans.vtt"

    def test_find_subtitle_ai_fallback(self, tmp_path):
        (tmp_path / "BVxx.ai-zh.vtt").write_text("AI")
        mode, f = vd._find_subtitle(tmp_path)
        assert mode == "AI"

    def test_find_subtitle_none(self, tmp_path):
        assert vd._find_subtitle(tmp_path) == (None, None)

    def test_friendly_error_403_cookie_hint(self):
        msg = vd._friendly_download_error(Exception("HTTP Error 403: Forbidden"))
        assert "403" in msg and "cookie" in msg

    def test_friendly_error_generic(self):
        assert vd._friendly_download_error(Exception("some failure")) == "B 站下载失败：some failure"
