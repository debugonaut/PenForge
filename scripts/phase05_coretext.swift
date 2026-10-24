import Foundation
import CoreText
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

let input = URL(fileURLWithPath: CommandLine.arguments[1])
let output = URL(fileURLWithPath: CommandLine.arguments[2])
var registrationError: Unmanaged<CFError>?
guard CTFontManagerRegisterFontsForURL(input as CFURL, .process, &registrationError) else { fatalError("process_registration_failed") }
defer { CTFontManagerUnregisterFontsForURL(input as CFURL, .process, nil) }
let descriptors = CTFontManagerCreateFontDescriptorsFromURL(input as CFURL) as! [CTFontDescriptor]
guard let descriptor = descriptors.first else { fatalError("descriptor_unavailable") }
let font = CTFontCreateWithFontDescriptor(descriptor, 64, nil)
let text = "AbcXYZ"
let string = NSAttributedString(string: text, attributes: [NSAttributedString.Key(kCTFontAttributeName as String): font])
let line = CTLineCreateWithAttributedString(string)
let runs = CTLineGetGlyphRuns(line) as! [CTRun]
var glyphCount = 0
for run in runs {
  let count = CTRunGetGlyphCount(run)
  var glyphs = [CGGlyph](repeating: 0, count: count)
  CTRunGetGlyphs(run, CFRange(location: 0, length: 0), &glyphs)
  guard !glyphs.contains(0) else { fatalError("notdef_for_covered_text") }
  glyphCount += count
}
guard glyphCount == text.count else { fatalError("glyph_count_mismatch") }
let width = 900, height = 220
let color = CGColorSpaceCreateDeviceRGB()
let context = CGContext(data:nil,width:width,height:height,bitsPerComponent:8,bytesPerRow:width*4,space:color,bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue)!
context.setFillColor(CGColor(gray:1,alpha:1));context.fill(CGRect(x:0,y:0,width:width,height:height))
context.setFillColor(CGColor(gray:0,alpha:1));context.textPosition=CGPoint(x:30,y:70);CTLineDraw(line,context)
let image=context.makeImage()!
let destination=CGImageDestinationCreateWithURL(output as CFURL,UTType.png.identifier as CFString,1,nil)!
CGImageDestinationAddImage(destination,image,nil)
guard CGImageDestinationFinalize(destination) else { fatalError("render_failed") }
print("{\"consumer\":\"macOS CoreText\",\"process_registration\":true,\"covered_glyphs\":\(glyphCount),\"notdef_count\":0,\"png_written\":true}")
