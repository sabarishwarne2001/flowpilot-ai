import React from "react";

type Tone = "default" | "strong" | "primary";

/**
 * A Night Bridge glass panel (docs/helm/VISUAL.md "Glass panel"). `enter` gives it the page
 * entrance (rise 12px and fade in), `index` its place in the 40 ms stagger.
 */
export const GlassPanel = <T extends React.ElementType = "section">({
  as,
  tone = "default",
  enter = false,
  index = 0,
  lift = false,
  className = "",
  style,
  children,
  ...rest
}: {
  readonly as?: T;
  readonly tone?: Tone;
  readonly enter?: boolean;
  readonly index?: number;
  readonly lift?: boolean;
  readonly className?: string;
  readonly style?: React.CSSProperties;
  readonly children?: React.ReactNode;
} & Omit<React.ComponentPropsWithoutRef<T>, "as" | "className" | "style" | "children">): React.ReactElement => {
  const Tag = (as ?? "section") as React.ElementType;
  const classes = [
    "helm-panel",
    tone === "strong" ? "helm-panel-strong" : "",
    tone === "primary" ? "helm-panel-primary" : "",
    lift ? "helm-panel-lift" : "",
    enter ? "helm-enter" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <Tag className={classes} style={enter ? { ...style, ["--i" as string]: index } : style} {...rest}>
      {children}
    </Tag>
  );
};

export default GlassPanel;
