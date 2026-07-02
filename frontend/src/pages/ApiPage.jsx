import React from "react";
import ApiExplorer from "../ApiExplorer.jsx";

export default function ApiPage() {
  return (
    <div>
      <h2 className="page-title">API explorer</h2>
      <ApiExplorer mode="common" />
    </div>
  );
}
