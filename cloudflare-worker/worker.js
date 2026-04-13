/**
 * Cloudflare Worker - TikHub 搜索代理
 * 接收关键词，并发搜索4个平台，过滤后返回结果
 *
 * 环境变量: TIKHUB_API_KEY
 * 调用方式: GET /search?keyword=存钱
 */

const TIKHUB_BASE = "https://api.tikhub.io";
const THREE_MONTHS_MS = 90 * 24 * 60 * 60 * 1000;

// 平台配置
const PLATFORMS = {
  xigua: { name: "西瓜视频", color: "#F04142" },
  bilibili: { name: "B站", color: "#00A1D6" },
  douyin: { name: "抖音", color: "#1A1A1A" },
  xiaohongshu: { name: "小红书", color: "#FF2442" },
};

// ── 各平台搜索 ──────────────────────────────────────

async function searchXigua(keyword, apiKey) {
  try {
    const url = `${TIKHUB_BASE}/api/v1/xigua/app/v2/search_video?keyword=${encodeURIComponent(keyword)}&order_type=play_count`;
    const resp = await fetch(url, {
      headers: { Authorization: `Bearer ${apiKey}` },
    });
    if (!resp.ok) return [];
    const data = await resp.json();
    const d = data?.data || {};
    const results = d.results || d.data || [];
    if (!Array.isArray(results)) return [];

    return results.slice(0, 15).map((item) => {
      const vdata = typeof item.data === "object" ? item.data : item;
      const groupId = vdata.group_id || vdata.gid || "";
      return {
        title: vdata.title || vdata.video_title || vdata.content || vdata.desc || "",
        url: vdata.share_url || vdata.url || (groupId ? `https://www.ixigua.com/${groupId}` : ""),
        play: (vdata.video_detail_info || {}).video_watch_count || vdata.video_watch_count || vdata.play_count || 0,
        like: vdata.digg_count || vdata.like_count || 0,
        comment: vdata.comment_count || 0,
        create_time: vdata.create_time || vdata.publish_time || 0,
      };
    }).filter((i) => i.title);
  } catch (e) {
    console.error("Xigua error:", e);
    return [];
  }
}

async function searchBilibili(keyword, apiKey) {
  try {
    const url = `${TIKHUB_BASE}/api/v1/bilibili/web/fetch_general_search?keyword=${encodeURIComponent(keyword)}&order=totalrank&page=1&page_size=15`;
    const resp = await fetch(url, {
      headers: { Authorization: `Bearer ${apiKey}` },
    });
    if (!resp.ok) return [];
    const data = await resp.json();
    const d = data?.data || {};
    const inner = d.data || d;
    let results = inner.result || [];
    if (!Array.isArray(results) || results.length === 0) {
      for (const v of Object.values(inner)) {
        if (Array.isArray(v) && v.length > 0) { results = v; break; }
      }
    }

    return results.slice(0, 15).map((item) => {
      const title = (item.title || item.name || "").replace(/<em class="keyword">/g, "").replace(/<\/em>/g, "");
      const bvid = item.bvid || "";
      return {
        title,
        url: bvid ? `https://www.bilibili.com/video/${bvid}` : item.arcurl || "",
        play: item.play || item.view || 0,
        like: item.like || item.favorites || 0,
        comment: 0,
        create_time: item.pubdate || item.senddate || 0,
      };
    }).filter((i) => i.title && (i.type || "") !== "bili_user");
  } catch (e) {
    console.error("Bilibili error:", e);
    return [];
  }
}

async function searchDouyin(keyword, apiKey) {
  try {
    const resp = await fetch(`${TIKHUB_BASE}/api/v1/douyin/search/fetch_general_search_v2`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ keyword, offset: 0, count: 15, sort_type: "0" }),
    });
    if (!resp.ok) return [];
    const data = await resp.json();
    const d = data?.data || {};
    let results = d.data || d.aweme_list || d.items || d.results || [];
    if (!Array.isArray(results)) {
      for (const v of Object.values(d)) {
        if (Array.isArray(v) && v.length > 0 && typeof v[0] === "object") { results = v; break; }
      }
    }

    return results.slice(0, 15).map((item) => {
      let aweme = item.aweme_info;
      if (!aweme && typeof item.data === "object") {
        aweme = item.data.aweme_info || item.data;
      }
      if (!aweme) aweme = item;
      const stats = aweme.statistics || {};
      return {
        title: aweme.desc || aweme.title || "",
        url: aweme.share_url || "",
        play: stats.play_count || aweme.play_count || 0,
        like: stats.digg_count || aweme.digg_count || 0,
        comment: 0,
        create_time: aweme.create_time || 0,
      };
    }).filter((i) => i.title);
  } catch (e) {
    console.error("Douyin error:", e);
    return [];
  }
}

async function searchXiaohongshu(keyword, apiKey) {
  try {
    const url = `${TIKHUB_BASE}/api/v1/xiaohongshu/app/search_notes?keyword=${encodeURIComponent(keyword)}&page=1`;
    const resp = await fetch(url, {
      headers: { Authorization: `Bearer ${apiKey}` },
    });
    if (!resp.ok) return [];
    const data = await resp.json();
    const d = data?.data || {};
    const inner = d.data || d;
    let results = inner.items || inner.notes || inner.note_list || inner.data || [];
    if (!Array.isArray(results)) {
      for (const v of Object.values(inner)) {
        if (Array.isArray(v) && v.length > 0 && typeof v[0] === "object") { results = v; break; }
      }
    }

    return results.slice(0, 15).map((item) => {
      const note = item.note_card || item.note || item;
      const noteId = note.note_id || note.id || item.id || "";
      let like = note.interact_info?.liked_count || note.liked_count || 0;
      if (typeof like === "string") {
        like = parseInt(like.replace("万", "0000")) || 0;
      }
      return {
        title: note.display_title || note.title || note.desc || note.name || "",
        url: noteId ? `https://www.xiaohongshu.com/explore/${noteId}` : "",
        play: 0,
        like,
        comment: 0,
        create_time: note.time || note.last_update_time || 0,
      };
    }).filter((i) => i.title);
  } catch (e) {
    console.error("Xiaohongshu error:", e);
    return [];
  }
}

// ── 过滤逻辑 ────────────────────────────────────────

function filterResults(platform, items) {
  const threeMonthsAgo = Math.floor((Date.now() - THREE_MONTHS_MS) / 1000);
  return items.filter((item) => {
    // 3个月时间过滤
    if (item.create_time && item.create_time < threeMonthsAgo) return false;
    return true;
  }).slice(0, 10);
}

// ── CORS 响应头 ─────────────────────────────────────

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Content-Type": "application/json; charset=utf-8",
};

// ── 主入口 ──────────────────────────────────────────

export default {
  async fetch(request, env) {
    // 处理 CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS_HEADERS });
    }

    const url = new URL(request.url);
    if (url.pathname !== "/search") {
      return new Response(JSON.stringify({ error: "Use /search?keyword=xxx" }), {
        status: 404,
        headers: CORS_HEADERS,
      });
    }

    const keyword = url.searchParams.get("keyword");
    if (!keyword) {
      return new Response(JSON.stringify({ error: "Missing keyword parameter" }), {
        status: 400,
        headers: CORS_HEADERS,
      });
    }

    const apiKey = env.TIKHUB_API_KEY;
    if (!apiKey) {
      return new Response(JSON.stringify({ error: "API key not configured" }), {
        status: 500,
        headers: CORS_HEADERS,
      });
    }

    // 并发搜索4个平台
    const [xigua, bilibili, douyin, xiaohongshu] = await Promise.all([
      searchXigua(keyword, apiKey),
      searchBilibili(keyword, apiKey),
      searchDouyin(keyword, apiKey),
      searchXiaohongshu(keyword, apiKey),
    ]);

    // 过滤
    const result = {
      keyword,
      timestamp: new Date().toISOString(),
      platforms: {
        xigua: filterResults("xigua", xigua),
        bilibili: filterResults("bilibili", bilibili),
        douyin: filterResults("douyin", douyin),
        xiaohongshu: filterResults("xiaohongshu", xiaohongshu),
      },
    };

    return new Response(JSON.stringify(result, null, 2), {
      headers: CORS_HEADERS,
    });
  },
};
