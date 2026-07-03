import React, { useState } from "react";
import { Routes, Route, NavLink } from "react-router-dom";
import Register from "./pages/Register.jsx";
import ChannelList from "./pages/ChannelList.jsx";
import ApiPage from "./pages/ApiPage.jsx";
import Insights from "./pages/Insights.jsx";
import ChannelConsole from "./pages/ChannelConsole.jsx";

const SAMSUNG_LOGO =
  "https://images.samsung.com/is/image/samsung/assets/in/tvs/smart-tv/samsung-tv-plus/samsung-tv-plus-icon.jpg";

const NAV = [
  ["/", "Register channel", "＋", true],
  ["/channels", "Channels", "☰", false],
  ["/insights", "Insights", "📊", false],
  ["/api", "API explorer", "{ }", false],
];

export default function App() {
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem("sidebarCollapsed") === "1");

  function toggle() {
    setCollapsed((c) => {
      const next = !c;
      localStorage.setItem("sidebarCollapsed", next ? "1" : "0");
      return next;
    });
  }

  return (
    <div className={`shell ${collapsed ? "collapsed" : ""}`}>
      <aside className="sidebar">
        <div className="side-top">
          <div className="brand">
            <img className="logo-img" src={SAMSUNG_LOGO} alt="Samsung TV Plus"
                 onError={(e) => { e.currentTarget.style.display = "none"; }} />
            {!collapsed && (
              <div className="brand-txt">
                <h1>TV Plus STUDIO</h1>
                <div className="sub">Instream Overlays</div>
              </div>
            )}
          </div>
        </div>

        <nav className="side-nav">
          {NAV.map(([to, label, icon, end]) => (
            <NavLink key={to} to={to} end={end} title={label}
                     className={({ isActive }) => `side-link ${isActive ? "active" : ""}`}>
              <span className="side-ic">{icon}</span>
              {!collapsed && <span className="side-label">{label}</span>}
            </NavLink>
          ))}
        </nav>

        <div className="side-bottom">
          <button className="side-toggle" onClick={toggle}
                  title={collapsed ? "Expand" : "Collapse"} aria-label="Toggle sidebar">
            <span className="side-ic">{collapsed ? "»" : "«"}</span>
            {!collapsed && <span className="side-label">Collapse</span>}
          </button>
        </div>
      </aside>

      <main className="content">
        <Routes>
          <Route path="/" element={<Register />} />
          <Route path="/channels" element={<ChannelList />} />
          <Route path="/insights" element={<Insights />} />
          <Route path="/api" element={<ApiPage />} />
          <Route path="/channel/:id" element={<ChannelConsole />} />
        </Routes>
      </main>
    </div>
  );
}
