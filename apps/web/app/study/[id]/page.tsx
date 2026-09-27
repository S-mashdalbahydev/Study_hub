import { Workspace } from "@/components/workspace";
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ session?: string; page?: string }>;
}) {
  const { id } = await params;
  const { session, page } = await searchParams;
  return (
    <Workspace
      documentId={id}
      sessionId={session}
      initialPage={Number(page) || undefined}
    />
  );
}
