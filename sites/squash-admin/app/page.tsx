import { requireChatGPTUser } from "./chatgpt-auth";
import Console from "./console";
export const dynamic = "force-dynamic";
export default async function Home() {
    await requireChatGPTUser("/");
    return <Console />;
}
