"""音频 HLS 解析与 AES 解密的离线单测（不依赖网络/浏览器）。"""
import pytest

from nhk_tool import fetcher
from nhk_tool.logger import DownloadError


def test_parse_media_playlist_extracts_key_and_segments():
    m3u8 = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-TARGETDURATION:7\n"
        '#EXT-X-KEY:METHOD=AES-128,URI="https://media.example/serve.key?t=abc"\n'
        "#EXTINF:6.016,\n"
        "index_64k_00001.aac?token=x\n"
        "#EXTINF:6.016,\n"
        "index_64k_00002.aac?token=x\n"
        "#EXT-X-ENDLIST\n"
    )
    key_url, segments = fetcher._parse_media_playlist(m3u8)
    assert key_url == "https://media.example/serve.key?t=abc"
    assert segments == [
        "index_64k_00001.aac?token=x",
        "index_64k_00002.aac?token=x",
    ]


def test_parse_media_playlist_no_key():
    m3u8 = "#EXTM3U\n#EXTINF:6,\nseg1.aac\n#EXT-X-ENDLIST\n"
    key_url, segments = fetcher._parse_media_playlist(m3u8)
    assert key_url is None
    assert segments == ["seg1.aac"]


class _FakeSession:
    """伪会话：按 URL 返回预置的 AES 加密分片字节，验证解密合并逻辑。"""

    def __init__(self, segments_plain, key):
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad

        self._map = {}
        self._key = key
        # 分片从序号 1 起，IV = 序号
        for i, plain in enumerate(segments_plain, start=1):
            iv = i.to_bytes(16, "big")
            enc = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(plain, 16))
            self._map[f"https://media.example/seg_{i}.aac"] = enc

    def _fetch_bytes(self, url):
        return 200, self._map[url]


def test_download_and_decrypt_roundtrip():
    key = bytes(range(16))
    plains = [b"hello-segment-1-data", b"second-chunk-2!!", b"3rd"]
    sess = _FakeSession(plains, key)
    segments = [f"seg_{i}.aac" for i in range(1, 4)]
    out = fetcher._download_and_decrypt(
        sess, "https://media.example/index.m3u8", segments, key
    )
    assert out == b"".join(plains)


def test_download_and_decrypt_unencrypted_concat():
    class Plain:
        def _fetch_bytes(self, url):
            return 200, b"X" if url.endswith("1.aac") else b"Y"

    out = fetcher._download_and_decrypt(
        Plain(), "https://m/index.m3u8", ["1.aac", "2.aac"], None
    )
    assert out == b"XY"


def test_download_and_decrypt_segment_failure_raises():
    class Bad:
        def _fetch_bytes(self, url):
            return 403, None

    with pytest.raises(DownloadError):
        fetcher._download_and_decrypt(
            Bad(), "https://m/index.m3u8", ["1.aac"], None
        )
