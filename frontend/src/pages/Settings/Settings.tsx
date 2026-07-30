import React, { useState } from "react";
import Workspace from "./Workspace";
import EmailSettings from "./EmailSettings";
import AISettings from "./AISettings";
import DocumentSettings from "./DocumentSettings";

const Settings: React.FC = () => {
  const [activeSection, setActiveSection] = useState<
    "workspace" | "email" | "ai" | "document"
  >("workspace");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Settings</h1>

        <p className="mt-2 text-muted-foreground">
          Manage your workspace configuration.
        </p>
      </div>

      <div className="grid grid-cols-12 gap-6">
        {/* Left Sidebar */}
        <aside className="col-span-3 rounded-xl border border-border bg-card p-4">
          <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Configuration
          </h2>

          <nav className="space-y-2">
            <button
              onClick={() => setActiveSection("workspace")}
              className={`w-full rounded-lg px-4 py-2 text-left transition-colors ${
                activeSection === "workspace"
                  ? "bg-primary text-primary-foreground"
                  : "hover:bg-muted"
              }`}
            >
              Workspace
            </button>

            <button
              onClick={() => setActiveSection("email")}
              className={`w-full rounded-lg px-4 py-2 text-left transition-colors ${
                activeSection === "email"
                  ? "bg-primary text-primary-foreground"
                  : "hover:bg-muted"
              }`}
            >
              Email
            </button>

            <button
              onClick={() => setActiveSection("ai")}
              className={`w-full rounded-lg px-4 py-2 text-left transition-colors ${
                activeSection === "ai"
                  ? "bg-primary text-primary-foreground"
                  : "hover:bg-muted"
              }`}
            >
              AI Settings
            </button>

            <button
              onClick={() => setActiveSection("document")}
              className={`w-full rounded-lg px-4 py-2 text-left transition-colors ${
                activeSection === "document"
                  ? "bg-primary text-primary-foreground"
                  : "hover:bg-muted"
              }`}
            >
              Document Settings
            </button>
          </nav>
        </aside>

        {/* Right Content */}
        <section className="col-span-9">
          {activeSection === "workspace" && <Workspace />}
          {activeSection === "email" && <EmailSettings />}
          {activeSection === "ai" && <AISettings />}
          {activeSection === "document" && <DocumentSettings />}
        </section>
      </div>
    </div>
  );
};

export default Settings;
