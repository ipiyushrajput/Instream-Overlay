/**
 * The page header.
 *
 * Persistent context: what this screen is and what state it is in. Actions sit on the right
 * so the eye travels title -> state -> action. It is full-bleed and sticky, so the operator
 * always knows where they are while a dense panel scrolls underneath.
 */
import React from "react";

export function PageHeader({ title, subtitle, actions }) {
  return (
    <header className="page-head">
      <h2 className="page-title">{title}</h2>
      {actions}
      {subtitle && <p className="page-sub">{subtitle}</p>}
    </header>
  );
}

export function PageBody({ children }) {
  return <div className="page-body">{children}</div>;
}
