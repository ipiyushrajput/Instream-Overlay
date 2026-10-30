/**
 * Icon set.
 *
 * Drawn inline rather than pulled from a package: the set is small, every glyph inherits
 * `currentColor` so it picks up the surrounding state, and the bundle stays lean.
 */
import React from "react";

function Svg({ size = 18, children }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

/** Register a channel — add. */
export const IconRegister = (p) => (
  <Svg {...p}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
);

/** Channels — stacked streams. */
export const IconChannels = (p) => (
  <Svg {...p}>
    <rect x="3" y="4" width="18" height="12" rx="2" />
    <path d="M8 20h8M12 16v4" />
  </Svg>
);

/** Insights — trend over time. */
export const IconInsights = (p) => (
  <Svg {...p}>
    <path d="M3 3v18h18" />
    <path d="m7 14 3.5-4 3 2.5L20 7" />
  </Svg>
);

/** API explorer — machine surface. */
export const IconApi = (p) => (
  <Svg {...p}>
    <path d="m8 7-5 5 5 5M16 7l5 5-5 5M14 4l-4 16" />
  </Svg>
);

export const IconCollapse = (p) => (
  <Svg {...p}>
    <path d="m14 7-5 5 5 5" />
    <path d="M19 4v16" />
  </Svg>
);

export const IconExpand = (p) => (
  <Svg {...p}>
    <path d="m10 7 5 5-5 5" />
    <path d="M5 4v16" />
  </Svg>
);
