import type { Metadata } from "next";

import { SiteFooter } from "@/components/site/footer";
import { SiteHeader } from "@/components/site/header";
import { PageHero } from "@/components/site/page-hero";
import { StatusBoard } from "@/components/site/status-board";
import { Container } from "@/components/ui";

export const metadata: Metadata = { title: "Status", description: "Gateway, model and job queue health." };

export default function Status() {
  return (
    <>
      <SiteHeader />
      <main>
        <PageHero eyebrow="Status" title="System status."
          lead="Live checks of the Gateway and Lorde Serve, refreshed every 30 seconds. Incidents follow the runbooks in the repository." />
        <Container className="py-16"><StatusBoard /></Container>
      </main>
      <SiteFooter />
    </>
  );
}
