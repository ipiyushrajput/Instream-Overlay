import React from "react";
import { Routes, Route, Link, useLocation } from "react-router-dom";
import Channels from "./pages/Channels.jsx";
import ChannelConsole from "./pages/ChannelConsole.jsx";

const SAMSUNG_LOGO =
  "https://images.samsung.com/is/image/samsung/assets/in/tvs/smart-tv/samsung-tv-plus/samsung-tv-plus-icon.jpg";

export default function App() {
  const loc = useLocation();
  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          <img className="logo-img" src={SAMSUNG_LOGO} alt="Samsung TV Plus"
               onError={(e) => { e.currentTarget.style.display = "none"; }} />
          <div>
            <h1>Instream Overlay</h1>
            <div className="sub">L-band &amp; in-stream overlay injection for live HLS</div>
          </div>
        </Link>
        <nav className="nav">
          <Link className={loc.pathname === "/" ? "active" : ""} to="/">Channels</Link>
        </nav>
      </header>

      <Routes>
        <Route path="/" element={<Channels />} />
        <Route path="/channel/:id" element={<ChannelConsole />} />
      </Routes>
    </div>
  );
}
