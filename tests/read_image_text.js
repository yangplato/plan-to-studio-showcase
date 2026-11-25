ObjC.import('Vision')
ObjC.import('Foundation')
function run(argv) {
  let request = $.VNRecognizeTextRequest.alloc.init
  request.recognitionLanguages = ['zh-Hans','en-US']
  const url = $.NSURL.fileURLWithPath(argv[0])
  const handler = $.VNImageRequestHandler.alloc.initWithURLOptions(url,$({}))
  if (!handler.performRequestsError($.NSArray.arrayWithObject(request),null)) throw Error('OCR failed')
  return ObjC.unwrap(request.results).map(r=>ObjC.unwrap(r.topCandidates(1).objectAtIndex(0).string)).join('\n')
}
