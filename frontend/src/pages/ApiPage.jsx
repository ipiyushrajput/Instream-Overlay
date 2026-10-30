import React from "react";
import ApiExplorer from "../ApiExplorer.jsx";
import { PageBody, PageHeader } from "../PageHeader.jsx";

export default function ApiPage() {
  return (
    <div>
      <PageHeader title="API explorer"
                  subtitle="Call the operator API directly and read the raw response." />
      <PageBody>
        <ApiExplorer mode="common" />
      </PageBody>
    </div>
  );
}
