const form = document.querySelector("#search");
const input = document.querySelector("#url");
const results = document.querySelector("#results");
const status = document.querySelector("#status");
const token = document.querySelector('meta[name="local-token"]').content;

function setBusy(busy) {
  for (const control of document.querySelectorAll("input, button")) {
    control.disabled = busy;
  }
}

async function request(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Local-Token": token},
    body: JSON.stringify(body),
    cache: "no-store",
    credentials: "omit",
  });
  if (!response.ok) {
    const details = await response.json();
    throw new Error(details.error || "本地请求失败。");
  }
  return response;
}

async function download(format) {
  setBusy(true);
  status.textContent = "";
  const panel = document.createElement("div");
  panel.className = "download-progress";
  const bar = document.createElement("progress");
  bar.max = 100;
  bar.setAttribute("aria-label", "音视频下载和合并进度");
  const percent = document.createElement("span");
  percent.textContent = "等待进度";
  const message = document.createElement("p");
  message.textContent = "正在下载音视频并合并中...";
  panel.append(bar, percent, message);
  status.before(panel);
  try {
    const response = await request("/api/download", {id: format.id});
    const {ticket} = await response.json();
    while (true) {
      const check = await request("/api/download-status", {ticket});
      const {sent, percent: value} = await check.json();
      if (typeof value === "number") {
        bar.value = value;
        percent.textContent = `${value}%`;
      }
      if (sent) {
        panel.classList.add("complete");
        message.textContent = "音视频处理完成";
        status.textContent = "已提交给 FDM，请在 FDM 中查看保存位置和下载进度。";
        break;
      }
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  } catch (error) {
    panel.remove();
    status.textContent = error.message;
  } finally {
    setBusy(false);
  }
}

function showResults(data) {
  const title = document.createElement("h2");
  title.textContent = data.title;
  results.append(title);
  const list = document.createElement("ul");
  for (const format of data.formats) {
    const row = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `${format.label} · 下载`;
    button.addEventListener("click", () => {
      document.querySelector(".download-progress")?.remove();
      download(format);
    });
    row.append(button);
    list.append(row);
  }
  results.append(list);
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  results.replaceChildren();
  document.querySelector(".download-progress")?.remove();
  setBusy(true);
  status.textContent = "";
  const loading = document.createElement("div");
  loading.className = "search-progress";
  loading.setAttribute("role", "status");
  loading.textContent = "正在解析链接，查询可用的高清 HLS 格式...";
  form.after(loading);
  try {
    const response = await request("/api/search", {url: input.value.trim()});
    const data = await response.json();
    input.value = "";
    showResults(data);
    status.textContent = `找到 ${data.formats.length} 个格式。`;
  } catch (error) {
    status.textContent = error.message;
  } finally {
    loading.remove();
    setBusy(false);
  }
});

window.addEventListener("pagehide", () => {
  input.value = "";
  results.replaceChildren();
  document.querySelector(".download-progress")?.remove();
  status.textContent = "";
});
