import { SiteFooter } from "@/components/site/footer";
import { SiteHeader } from "@/components/site/header";
import { Container, LinkButton } from "@/components/ui";

export default function NotFound() {
  return (
    <>
      <SiteHeader />
      <main>
        <Container className="flex min-h-[60vh] flex-col items-center justify-center py-24 text-center">
          <p className="font-mono text-[12px] uppercase tracking-[0.14em] text-muted">404 · verdict: not found</p>
          <h1 className="mt-4 text-[48px] font-medium tracking-[-0.045em]">This page didn&apos;t pass the gate.</h1>
          <p className="mt-3 text-muted">It may have moved, or never existed.</p>
          <div className="mt-8 flex gap-3"><LinkButton href="/">Home</LinkButton><LinkButton href="/docs" variant="secondary">Docs</LinkButton></div>
        </Container>
      </main>
      <SiteFooter />
    </>
  );
}
