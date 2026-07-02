import React from "react";
import { Routes, Route, NavLink } from "react-router-dom";
import Register from "./pages/Register.jsx";
import ChannelList from "./pages/ChannelList.jsx";
import ApiPage from "./pages/ApiPage.jsx";
import ChannelConsole from "./pages/ChannelConsole.jsx";

const SAMSUNG_LOGO =
  "https://images.samsung.com/is/image/samsung/assets/in/tvs/smart-tv/samsung-tv-plus/samsung-tv-plus-icon.jpg";

const NAV = [
  ["/", "Register channel", "＋", true],
  ["/channels", "Channels", "☰", false],
  ["/api", "API explorer", "{ }", false],
];

export default function App() {
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <img className="logo-img" src={SAMSUNG_LOGO} alt="Samsung TV Plus"
               onError={(e) => { e.currentTarget.style.display = "none"; }} />
          <div>
            <h1>Instream Overlay</h1>
            <div className="sub">Live HLS overlays</div>
          </div>
        </div>
        <nav className="side-nav">
          {NAV.map(([to, label, icon, end]) => (
            <NavLink key={to} to={to} end={end}
                     className={({ isActive }) => `side-link ${isActive ? "active" : ""}`}>
              <span className="side-ic">{icon}</span>{label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <main className="content">
        <Routes>
          <Route path="/" element={<Register />} />
          <Route path="/channels" element={<ChannelList />} />
          <Route path="/api" element={<ApiPage />} />
          <Route path="/channel/:id" element={<ChannelConsole />} />
        </Routes>
      </main>
    </div>
  );
}
