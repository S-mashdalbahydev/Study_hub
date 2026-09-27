import { NextResponse, type NextRequest } from "next/server";

// Optimistic routing only: the API checks every request itself. A missing cookie sends a
// visitor to sign up (from the front page) or to log in and then back to where they were.
const PUBLIC = new Set(["/login", "/signup"]);

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  if (PUBLIC.has(pathname) || request.cookies.has("studyhub_session")) {
    return NextResponse.next();
  }
  const url = request.nextUrl.clone();
  url.search = "";
  if (pathname === "/") {
    url.pathname = "/signup";
  } else {
    url.pathname = "/login";
    url.searchParams.set("next", pathname + search);
  }
  return NextResponse.redirect(url);
}

export const config = {
  // /backend is the API proxy: the API answers 401 itself.
  matcher: ["/((?!backend|_next|icon.svg|favicon.ico).*)"],
};
