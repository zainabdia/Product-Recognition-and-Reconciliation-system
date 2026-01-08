const API = "";

function qs(k){ return new URLSearchParams(location.search).get(k); }
function el(id){ return document.getElementById(id); }

function saveAuth({token, role, username}){
  localStorage.setItem("token", token);
  localStorage.setItem("role", role);
  localStorage.setItem("username", username);
}
function auth(){
  return {
    token: localStorage.getItem("token"),
    role: localStorage.getItem("role"),
    username: localStorage.getItem("username")
  };
}
function logout(){
  localStorage.removeItem("token");
  localStorage.removeItem("role");
  localStorage.removeItem("username");
  location.href = "/app/login.html";
}

async function apiGet(path, opts={}){
  const {token} = auth();
  const url = new URL(API + path, location.origin);
  if (token) url.searchParams.set("token", token);
  const res = await fetch(url.toString(), { ...opts, method:"GET" });
  const txt = await res.text();
  if(!res.ok) throw new Error(txt);
  return txt ? JSON.parse(txt) : {};
}

async function apiPost(path, body, opts={}){
  const {token} = auth();
  const url = new URL(API + path, location.origin);
  if (token) url.searchParams.set("token", token);
  const res = await fetch(url.toString(), {
    method:"POST",
    headers: {"Content-Type":"application/json", ...(opts.headers||{})},
    body: JSON.stringify(body)
  });
  const txt = await res.text();
  if(!res.ok) throw new Error(txt);
  return txt ? JSON.parse(txt) : {};
}

async function apiPatch(path, body){
  const {token} = auth();
  const url = new URL(API + path, location.origin);
  if (token) url.searchParams.set("token", token);
  const res = await fetch(url.toString(), {
    method:"PATCH",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(body)
  });
  const txt = await res.text();
  if(!res.ok) throw new Error(txt);
  return txt ? JSON.parse(txt) : {};
}

function setActiveNav(){
  const p = location.pathname.split("/").pop();
  document.querySelectorAll(".nav a").forEach(a=>{
    if(a.getAttribute("data-page") === p) a.classList.add("active");
  });
  const {role, username} = auth();
  if(el("who")){
    el("who").textContent = username ? `${username} (${role})` : "not logged in";
  }
}

function mustLogin(){
  const {token} = auth();
  if(!token) location.href = "/app/login.html";
}

function mustRole(allowed){
  const {role} = auth();
  if(!allowed.includes(role)){
    location.href = "/app/login.html";
  }
}

function confDot(conf){
  if(conf === null || conf === undefined) return "dot";
  if(conf >= 0.7) return "dot ok";
  if(conf >= 0.4) return "dot warn";
  return "dot bad";
}

function escapeHtml(s){
  return String(s ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
}
