/** Page shell: sidebar on the left, header + content on the right. */

import type { JSX, ReactNode } from 'react';

export interface LayoutProps {
  sidebar: ReactNode;
  headerTitle: string;
  headerControls?: ReactNode;
  children: ReactNode;
}

export function Layout({
  sidebar,
  headerTitle,
  headerControls,
  children,
}: LayoutProps): JSX.Element {
  return (
    <div className="layout">
      {sidebar}
      <div className="layout__main">
        <header className="layout__header">
          <h2 className="layout__title">{headerTitle}</h2>
          {headerControls}
        </header>
        <div className="layout__body">{children}</div>
      </div>
    </div>
  );
}
